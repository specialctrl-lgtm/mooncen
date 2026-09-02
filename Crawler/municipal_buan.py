"""Fail-closed collector for Buan-gun's integrated education ledger.

The official owner is the ``/reserve`` education area.  Its four active
navigation entries are treated as one ledger: lifelong learning,
Onggijonggi Culture Center, Buan Arts Center, and the Media Center board.
The separate ``/bale`` lifelong-learning site is an announcement portal.  It
links back to this ledger for registration, but also carries external and
notice-only programmes, so it is neither a second integrated-reservation
owner nor an exact duplicate course ledger.

Buan clamps requests after the last page to the last page instead of returning
an empty boundary.  A snapshot is published only after the advertised last
page, the exact clamped sentinel, and stable first/last/sentinel rechecks all
agree for every active category.  Lifelong-learning branch tabs are also
walked and reconciled with the unfiltered category ledger.

Only current/future detail pages are fetched.  Application controls are
inspected but application or applicant endpoints are never requested.  The
returned rows intentionally exclude instructor/contact data, free-text bodies,
images, and attachments.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BUAN_HOST = "www.buan.go.kr"
BUAN_PROVIDER = "MUNI_WWW_BUAN_GO_KR_B5BDBAE0"
BUAN_PORTAL_PROVIDER = "MUNI_WWW_BUAN_GO_KR_8AA2A8DE"
BUAN_MUNICIPALITY_CODE = "5280000000"
BUAN_MUNICIPALITY_NAME = "전북특별자치도 부안군"
BUAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_A28D095CAB64"
BUAN_DISCOVERY_PLAYGROUND_CANDIDATE_ID = "MUNI_IR_722CFEF5FDF4"
BUAN_PORTAL_CANDIDATE_ID = "MUNI_IR_5E78ECF316B2"
BUAN_CANONICAL_URL = (
    "https://www.buan.go.kr/reserve/index.buan?"
    "menuCd=DOM_000002001000000000"
)
BUAN_PORTAL_URL = (
    "https://www.buan.go.kr/bale/index.buan?"
    "menuCd=DOM_000002105001000000"
)
BUAN_PAGE_SIZE = 9
BUAN_MAX_PAGES = 10
BUAN_FETCH_ATTEMPTS = 2
BUAN_PARSER = (
    "buan_complete_integrated_education+exact_active_menu_vocabulary+"
    "advertised_last_page+clamped_last_sentinel+stable_boundaries+"
    "lifelong_branch_reconciliation+current_detail_summary_only+"
    "known_source_date_correction+aggregate_test_rejection+"
    "application_control_no_form_fetch+pii_allowlist"
)
BUAN_OWNERSHIP_SCOPE = "official_buan_integrated_reservation_active_education_menus"

BUAN_CANDIDATE_DECISIONS: Mapping[str, str] = {
    BUAN_CANONICAL_CANDIDATE_ID: "retarget_and_schedule_existing_complete_integrated_education_owner",
    BUAN_DISCOVERY_PLAYGROUND_CANDIDATE_ID: "exclude_child_playground_facility_information",
    BUAN_PORTAL_CANDIDATE_ID: "exclude_from_integrated_coverage_keep_separate_lifelong_notice_owner",
}


@dataclass(frozen=True)
class BuanCategory:
    key: str
    label: str
    url: str
    domain_category: str
    list_kind: str


BUAN_CATEGORIES = (
    BuanCategory(
        "lifelong",
        "평생학습",
        "https://www.buan.go.kr/reserve/index.buan?"
        "menuCd=DOM_000002001001000000&rsvCateSid=22",
        "평생교육",
        "reservation",
    ),
    BuanCategory(
        "culture",
        "옹기종기문화센터",
        "https://www.buan.go.kr/reserve/index.buan?"
        "menuCd=DOM_000002001002000000&rsvCateSid=45",
        "문화센터",
        "reservation",
    ),
    BuanCategory(
        "arts",
        "예술회관",
        "https://www.buan.go.kr/reserve/index.buan?"
        "menuCd=DOM_000002001003000000&rsvCateSid=46",
        "문화예술",
        "reservation",
    ),
    BuanCategory(
        "media",
        "미디어센터",
        "https://www.buan.go.kr/reserve/board/list.buan?"
        "boardId=BBS_0000237&menuCd=DOM_000002001007000000&"
        "contentsSid=2754&cpath=%2Freserve",
        "미디어교육",
        "board",
    ),
)
BUAN_CATEGORY_BY_KEY = {category.key: category for category in BUAN_CATEGORIES}
BUAN_ACTIVE_MENU_LABELS = tuple(category.label for category in BUAN_CATEGORIES)
BUAN_LIFELONG_BRANCHES = ("모두배움터", "청우평생학습관")

BUAN_BRANCH_ADDRESSES: Mapping[str, str] = {
    "청우평생학습관": "전북특별자치도 부안군 부안읍 당간지주1길 14-23",
    "옹기종기문화센터": "전북특별자치도 부안군 부안읍 매창로 127",
    "부안예술회관": "전북특별자치도 부안군 부안읍 예술회관길 11",
    "부안미디어센터": "전북특별자치도 부안군 부안읍 예술회관길 11",
}

# These routes are public but are not members of the current education menu.
BUAN_EXCLUDED_OWNER_BOUNDARIES: Mapping[str, str] = {
    "https://www.buan.go.kr/reserve/index.buan?menuCd=DOM_000002001003000000&rsvCateSid=85": (
        "unlinked_test_category_containing_only_test_data"
    ),
    "https://www.buan.go.kr/reserve/index.buan?menuCd=DOM_000002001004000000": (
        "removed_legacy_sports_course_route_not_active_education_navigation"
    ),
    "https://www.buan.go.kr/reserve/index.buan?menuCd=DOM_000002001005000000&rsvCateSid=48": (
        "commented_legacy_agriculture_route_currently_error_page"
    ),
    "https://www.buan.go.kr/reserve/index.buan?menuCd=DOM_000002002007000000": (
        "candidate_discovery_url_is_child_playground_facility_information"
    ),
}


@dataclass(frozen=True)
class BuanSourceCorrection:
    category_key: str
    identity: str
    source_end: date
    corrected_end: date
    evidence: str


# The official HTML says 2027-06-22, while its official poster says
# "2월~6월 ... 총 31회" and the title says 2026 상반기.  Keeping 2027 would
# create a one-year phantom current course, so the audited poster date wins.
BUAN_SOURCE_CORRECTIONS = (
    BuanSourceCorrection(
        "media",
        "358030",
        date(2027, 6, 22),
        date(2026, 6, 22),
        "official poster: 2026년 2월~6월, 매주 월·수, 총 31회",
    ),
)
_CORRECTION_BY_ID = {
    (item.category_key, item.identity): item for item in BUAN_SOURCE_CORRECTIONS
}


class BuanContractError(RuntimeError):
    """Raised when Buan's audited public contract changes."""


@dataclass(frozen=True)
class _ListedCourse:
    category_key: str
    identity: str
    title: str
    detail_url: str
    category_label: str
    fee: str
    venue: str
    target: str
    schedule: str
    capacity: str
    period: str
    apply_period: str
    event_start: date
    event_end: date
    raw_event_end: date
    apply_start: date
    apply_end: date
    page: int
    branch: str = ""


@dataclass(frozen=True)
class _Page:
    requested_page: int
    advertised_last_page: int
    rows: tuple[_ListedCourse, ...]
    branch_tabs: tuple[tuple[str, str], ...] = ()


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\.)?(?!\d)")
_BURE_RE = re.compile(r"^BURE\d{7}$")
_DATA_SID_RE = re.compile(r"^[1-9]\d*$")
_LINK_PAGE_RE = re.compile(r"link_go\(\s*(\d+)\s*\)")
_TEST_TITLE_RE = re.compile(r"^테스트(?:입니다)?[.!]?$")
_MEDIA_AGGREGATE_RE = re.compile(
    r"^20\d{2}년\s+제\d+기\s+미디어교육(?:\s+교육생\s+모집)?$"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


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


def _canonical_query(url: str) -> tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        (parsed.hostname or "").rstrip(".").lower(),
        parsed.path,
        tuple(sorted((key, tuple(values)) for key, values in query.items())),
    )


def is_buan_education_target(target: Any) -> bool:
    parsed = urlparse(_target_url(target))
    return bool(
        _provider(target) == BUAN_PROVIDER
        and parsed.scheme.lower() == "https"
        and parsed.port is None
        and not parsed.username
        and not parsed.password
        and not parsed.params
        and not parsed.fragment
        and _canonical_query(_target_url(target)) == _canonical_query(BUAN_CANONICAL_URL)
    )


is_target = is_buan_education_target


def is_buan_lifelong_notice_target(target: Any) -> bool:
    return bool(
        _provider(target) == BUAN_PORTAL_PROVIDER
        and _canonical_query(_target_url(target)) == _canonical_query(BUAN_PORTAL_URL)
    )


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


def _coerce_html(result: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    final_url = requested_url
    if isinstance(result, BeautifulSoup):
        soup = result
    elif isinstance(result, str):
        soup = BeautifulSoup(result, "html.parser")
    elif isinstance(result, bytes):
        soup = BeautifulSoup(result, "html.parser")
    else:
        status = getattr(result, "status_code", 200)
        if int(status) != 200:
            raise BuanContractError(f"HTTP {status} for {requested_url}")
        final_url = _clean(getattr(result, "url", requested_url)) or requested_url
        soup = BeautifulSoup(getattr(result, "text", ""), "html.parser")
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUAN_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
    ):
        raise BuanContractError(f"unexpected redirect target: {final_url}")
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if not title or "RFC 3.0 오류" in title or "오류 메세지" in title:
        raise BuanContractError(f"error or missing page title for {requested_url}: {title}")
    return soup, final_url


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
) -> BeautifulSoup:
    meta["logical_requests"] = int(meta.get("logical_requests") or 0) + 1
    error: Optional[Exception] = None
    for _ in range(BUAN_FETCH_ATTEMPTS):
        meta["physical_requests"] = int(meta.get("physical_requests") or 0) + 1
        try:
            soup, _ = _coerce_html(fetcher(session, url, timeout), url)
            return soup
        except Exception as exc:  # retried, then the complete snapshot fails closed
            error = exc
    meta["request_retry_count"] = int(meta["physical_requests"]) - int(meta["logical_requests"])
    if isinstance(error, BuanContractError):
        raise error
    raise BuanContractError(f"request failed after retries for {url}: {_clean(error)}") from error


def _category_page_url(category: BuanCategory, page: int, branch: str = "") -> str:
    if page < 1:
        raise BuanContractError("page must be positive")
    parsed = urlparse(category.url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if category.list_kind == "board":
        query.update(
            {
                "listRow": [str(BUAN_PAGE_SIZE)],
                "listCel": ["1"],
                "paging": ["ok"],
                "startPage": [str(page)],
            }
        )
    else:
        query["pageIndex"] = [str(page)]
        if branch:
            query["eduPlaceCode"] = [branch]
    return parsed._replace(query=urlencode(query, doseq=True)).geturl()


def _pairs(card: BeautifulSoup) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in card.select("dd"):
        strong = item.select_one("strong")
        if strong is None:
            continue
        key = _clean(strong.get_text(" ", strip=True))
        value = _clean(item.get_text(" ", strip=True))
        if key and value.startswith(key):
            value = _clean(value[len(key) :])
        if key and value:
            output[key] = value
    return output


def _date_range(value: str, label: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise BuanContractError(f"{label}: expected exactly two dates: {_clean(value)}")
    try:
        start, end = (date(int(y), int(m), int(d)) for y, m, d in matches)
    except ValueError as exc:
        raise BuanContractError(f"{label}: invalid date: {_clean(value)}") from exc
    if end < start:
        raise BuanContractError(f"{label}: end precedes start")
    return start, end


def _safe_detail_url(page_url: str, href: str, category: BuanCategory) -> tuple[str, str]:
    url = urljoin(page_url, _clean(href))
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUAN_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise BuanContractError(f"{category.key}: unsafe detail URL")
    if category.list_kind == "board":
        identity = _clean((query.get("dataSid") or [""])[0])
        if (
            parsed.path != "/reserve/board/view.buan"
            or (query.get("boardId") or [""])[0] != "BBS_0000237"
            or not _DATA_SID_RE.fullmatch(identity)
        ):
            raise BuanContractError(f"{category.key}: unexpected board detail URL: {url}")
    else:
        identity = _clean((query.get("reUniqId") or [""])[0])
        if (
            parsed.path not in {"/index.buan", "/reserve/index.buan"}
            or not _BURE_RE.fullmatch(identity)
            or (query.get("rsvCateSid") or [""])[0]
            != (parse_qs(urlparse(category.url).query).get("rsvCateSid") or [""])[0]
        ):
            raise BuanContractError(f"{category.key}: unexpected reservation detail URL: {url}")
    return url, identity


def _parse_card(
    card: BeautifulSoup,
    page_url: str,
    page: int,
    category: BuanCategory,
) -> _ListedCourse:
    title_scope = card.select_one("dt")
    title_link = card.select_one("dt a[href]") or card.select_one("a.sbtn_go[href]")
    if title_scope is None or title_link is None:
        raise BuanContractError(f"{category.key} page {page}: course card lacks title/detail link")
    title = _clean(title_link.get("title") or title_scope.get_text(" ", strip=True))
    if not title:
        raise BuanContractError(f"{category.key} page {page}: empty title")
    detail_url, identity = _safe_detail_url(page_url, title_link.get("href"), category)
    pairs = _pairs(card)
    period = _clean(pairs.get("교육기간"))
    apply_period = _clean(pairs.get("접수기간"))
    event_start, raw_event_end = _date_range(period, f"{category.key}:{identity} 교육기간")
    apply_start, apply_end = _date_range(apply_period, f"{category.key}:{identity} 접수기간")
    correction = _CORRECTION_BY_ID.get((category.key, identity))
    event_end = raw_event_end
    if correction is not None:
        if raw_event_end != correction.source_end:
            raise BuanContractError(
                f"{category.key}:{identity}: audited source correction no longer matches"
            )
        event_end = correction.corrected_end
    badges = [
        _clean(item.get_text(" ", strip=True))
        for item in card.select("span i")
        if _clean(item.get_text(" ", strip=True))
    ]
    fee = _clean(pairs.get("교육비") or (badges[1] if len(badges) > 1 else ""))
    if fee == "무료":
        fee = "0원"
    return _ListedCourse(
        category_key=category.key,
        identity=identity,
        title=title,
        detail_url=detail_url,
        category_label=_clean(badges[0] if badges else category.label),
        fee=fee,
        venue=_clean(pairs.get("진행장소") or pairs.get("교육장소")),
        target=_clean(pairs.get("이용대상") or pairs.get("교육대상")),
        schedule=_clean(pairs.get("교육시간") or pairs.get("요일/시간")),
        capacity=_clean(pairs.get("모집정원") or pairs.get("모집인원")),
        period=period,
        apply_period=apply_period,
        event_start=event_start,
        event_end=event_end,
        raw_event_end=raw_event_end,
        apply_start=apply_start,
        apply_end=apply_end,
        page=page,
    )


def _advertised_last_page(soup: BeautifulSoup, category: BuanCategory) -> int:
    values = {1}
    if category.list_kind == "board":
        for link in soup.select(".bbs_page a[href], a[href*='startPage=']"):
            query = parse_qs(urlparse(urljoin(category.url, link.get("href"))).query)
            value = _clean((query.get("startPage") or [""])[0])
            if value.isdigit():
                values.add(int(value))
    else:
        for link in soup.select(".bbs_page a[onclick]"):
            match = _LINK_PAGE_RE.search(_clean(link.get("onclick")))
            if match:
                values.add(int(match.group(1)))
        current = soup.select_one(".bbs_page a.on")
        if current is not None and _clean(current.get_text(" ", strip=True)).isdigit():
            values.add(int(_clean(current.get_text(" ", strip=True))))
    return max(values)


def _branch_tabs(soup: BeautifulSoup, category: BuanCategory) -> tuple[tuple[str, str], ...]:
    if category.key != "lifelong":
        return ()
    output: list[tuple[str, str]] = []
    for link in soup.select(".basic_tab2 a[href]"):
        label = _clean(link.get_text(" ", strip=True))
        if label == "전체":
            continue
        url = urljoin(category.url, link.get("href"))
        query = parse_qs(urlparse(url).query)
        branch = _clean((query.get("eduPlaceCode") or [""])[0])
        if not label or label != branch:
            raise BuanContractError("lifelong branch tab label/value mismatch")
        output.append((label, url))
    if tuple(label for label, _ in output) != BUAN_LIFELONG_BRANCHES:
        raise BuanContractError(
            f"lifelong branch vocabulary changed: {tuple(label for label, _ in output)}"
        )
    return tuple(output)


def _parse_page(
    soup: BeautifulSoup,
    page_url: str,
    requested_page: int,
    category: BuanCategory,
) -> _Page:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "교육강좌" not in title or category.label not in title:
        raise BuanContractError(
            f"{category.key} page {requested_page}: unexpected page title: {title}"
        )
    cards = soup.select(".ed_list > div")
    meaningful = [
        card
        for card in cards
        if "프로그램이 없습니다" not in _clean(card.get_text(" ", strip=True))
    ]
    if cards and not meaningful:
        if len(cards) != 1:
            raise BuanContractError(f"{category.key}: malformed empty-list marker")
        rows: tuple[_ListedCourse, ...] = ()
    else:
        rows = tuple(
            _parse_card(card, page_url, requested_page, category) for card in meaningful
        )
    if len(rows) > BUAN_PAGE_SIZE:
        raise BuanContractError(f"{category.key} page {requested_page}: page-size overflow")
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise BuanContractError(f"{category.key} page {requested_page}: duplicate identities")
    return _Page(
        requested_page=requested_page,
        advertised_last_page=_advertised_last_page(soup, category),
        rows=rows,
        branch_tabs=_branch_tabs(soup, category),
    )


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.advertised_last_page,
        tuple(
            (
                row.identity,
                row.title,
                row.period,
                row.apply_period,
                row.venue,
                row.capacity,
            )
            for row in page.rows
        ),
        page.branch_tabs,
    )


def _collect_pages(
    session: Any,
    category: BuanCategory,
    timeout: int,
    max_pages: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
    *,
    branch: str = "",
) -> tuple[list[_ListedCourse], _Page, int]:
    first_url = _category_page_url(category, 1, branch)
    first = _parse_page(
        _fetch_soup(session, first_url, timeout, fetcher, meta),
        first_url,
        1,
        category,
    )
    last_page = first.advertised_last_page
    if last_page < 1 or last_page > max_pages:
        meta["source_cap_reached"] = True
        raise BuanContractError(
            f"{category.key}{':' + branch if branch else ''}: advertised last page "
            f"{last_page} exceeds max_pages={max_pages}"
        )
    pages: dict[int, _Page] = {1: first}
    for page_number in range(2, last_page + 1):
        url = _category_page_url(category, page_number, branch)
        parsed = _parse_page(
            _fetch_soup(session, url, timeout, fetcher, meta),
            url,
            page_number,
            category,
        )
        if parsed.advertised_last_page != last_page:
            raise BuanContractError(f"{category.key}: advertised last page changed")
        pages[page_number] = parsed
    for page_number in range(1, last_page):
        if len(pages[page_number].rows) != BUAN_PAGE_SIZE:
            raise BuanContractError(
                f"{category.key} page {page_number}: premature short page"
            )
    sentinel_number = last_page + 1
    sentinel_url = _category_page_url(category, sentinel_number, branch)
    sentinel = _parse_page(
        _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
        sentinel_url,
        sentinel_number,
        category,
    )
    if _page_signature(sentinel) == _page_signature(pages[last_page]):
        boundary_mode = "clamped_last_page"
    elif not sentinel.rows:
        # Buan is inconsistent here: most unfiltered ledgers clamp, while a
        # short branch-filtered ledger returns a structural empty page.  Both
        # are explicit and are independently rechecked below.
        boundary_mode = "structural_empty"
    else:
        raise BuanContractError(
            f"{category.key}: post-last request was neither exact clamp nor empty"
        )
    scope_key = f"{category.key}:{branch}" if branch else category.key
    meta.setdefault("boundary_modes", {})[scope_key] = boundary_mode

    recheck_numbers = [1]
    if last_page > 1:
        recheck_numbers.append(last_page)
    for page_number in recheck_numbers:
        url = _category_page_url(category, page_number, branch)
        checked = _parse_page(
            _fetch_soup(session, url, timeout, fetcher, meta),
            url,
            page_number,
            category,
        )
        if _page_signature(checked) != _page_signature(pages[page_number]):
            raise BuanContractError(f"{category.key} page {page_number}: stability recheck changed")
    sentinel_checked = _parse_page(
        _fetch_soup(session, sentinel_url, timeout, fetcher, meta),
        sentinel_url,
        sentinel_number,
        category,
    )
    if _page_signature(sentinel_checked) != _page_signature(sentinel):
        raise BuanContractError(f"{category.key}: clamped sentinel recheck changed")

    rows = [row for number in sorted(pages) for row in pages[number].rows]
    identities = [row.identity for row in rows]
    if len(identities) != len(set(identities)):
        raise BuanContractError(f"{category.key}: duplicate identities across pages")
    return rows, first, last_page


def _menu_key(link: BeautifulSoup) -> str:
    label = _clean(link.get_text(" ", strip=True))
    href = _clean(link.get("href"))
    parsed = urlparse(urljoin(BUAN_CANONICAL_URL, href))
    query = parse_qs(parsed.query)
    menu = _clean((query.get("menuCd") or [""])[0])
    sid = _clean((query.get("rsvCateSid") or [""])[0])
    if label == "평생학습" and menu == "DOM_000002001001000000" and not sid:
        return "lifelong"
    if label == "옹기종기문화센터" and menu == "DOM_000002001002000000" and not sid:
        return "culture"
    if label == "예술회관" and menu == "DOM_000002001003000000" and not sid:
        return "arts"
    if label == "미디어센터" and menu == "DOM_000002001007000000" and not sid:
        return "media"
    return ""


def _validate_active_menu(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "교육강좌" not in title:
        raise BuanContractError(f"canonical education root changed: {title}")
    links = soup.select(".menu1 .depth_boxcon a[href]")
    selected: list[tuple[str, str]] = []
    for link in links:
        label = _clean(link.get_text(" ", strip=True))
        if label:
            selected.append((label, _menu_key(link)))
    # Some synthetic/minimal pages expose only the local category strip.
    if not selected:
        selected = [
            (_clean(link.get_text(" ", strip=True)), _menu_key(link))
            for link in soup.select(".bbs_cate a[href]")
        ]
    expected = [(category.label, category.key) for category in BUAN_CATEGORIES]
    if selected != expected:
        raise BuanContractError(f"active education menu vocabulary changed: {selected}")


def _detail_pairs(scope: BeautifulSoup) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in scope.select(".bbs_vtop li"):
        strong = item.select_one("strong")
        span = item.select_one("span")
        if strong is None or span is None:
            continue
        key = _clean(strong.get_text(" ", strip=True).split("*", 1)[0])
        value = _clean(span.get_text(" ", strip=True))
        if key and value:
            output[key] = value
    return output


def _application_control(scope: BeautifulSoup, detail_url: str) -> tuple[bool, str]:
    matches: list[BeautifulSoup] = []
    for link in scope.select("a[href], a[onclick]"):
        label = _clean(link.get_text(" ", strip=True))
        classes = " ".join(link.get("class") or [])
        if any(word in label for word in ("신청하기", "수강신청", "예약하기")) or (
            "bbs_bt2" in classes and "신청" in label
        ):
            matches.append(link)
    if len(matches) > 1:
        raise BuanContractError("detail exposes multiple ambiguous application controls")
    if not matches:
        return False, ""
    link = matches[0]
    href = _clean(link.get("href"))
    onclick = _clean(link.get("onclick"))
    application_url = ""
    if href and href != "#" and not href.lower().startswith("javascript:"):
        application_url = urljoin(detail_url, href)
        parsed = urlparse(application_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise BuanContractError("unsafe application control URL")
        lowered = application_url.lower()
        if any(token in lowered for token in ("applicant", "applicantlist", "applylist", "mberlist")):
            raise BuanContractError("application control points to an applicant-list endpoint")
    elif not onclick:
        raise BuanContractError("application control has no usable href/onclick")
    return True, application_url


def _parse_detail(
    soup: BeautifulSoup,
    listed: _ListedCourse,
) -> dict[str, Any]:
    scope = soup.select_one(".bbs_view") or soup.select_one("#content") or soup
    heading = scope.select_one(".bbs_vtop h4") or scope.select_one("h4")
    title = _clean(heading.get_text(" ", strip=True) if heading else "")
    if title != listed.title:
        raise BuanContractError(
            f"{listed.category_key}:{listed.identity}: detail title mismatch"
        )
    pairs = _detail_pairs(scope)
    period = _clean(pairs.get("교육기간"))
    apply_period = _clean(pairs.get("접수기간"))
    event_start, raw_event_end = _date_range(
        period, f"{listed.category_key}:{listed.identity} detail 교육기간"
    )
    apply_start, apply_end = _date_range(
        apply_period, f"{listed.category_key}:{listed.identity} detail 접수기간"
    )
    if (
        event_start != listed.event_start
        or raw_event_end != listed.raw_event_end
        or apply_start != listed.apply_start
        or apply_end != listed.apply_end
    ):
        raise BuanContractError(
            f"{listed.category_key}:{listed.identity}: list/detail dates disagree"
        )
    control, application_url = _application_control(scope, listed.detail_url)
    venue = _clean(pairs.get("교육장소") or pairs.get("진행장소"))
    if listed.venue and venue and listed.venue != venue:
        raise BuanContractError(
            f"{listed.category_key}:{listed.identity}: list/detail venue mismatch"
        )
    return {
        "title": title,
        "period": period,
        "apply_period": apply_period,
        "schedule": _clean(pairs.get("교육시간")) or listed.schedule,
        "target": _clean(pairs.get("교육대상") or pairs.get("이용대상")) or listed.target,
        "venue": venue or listed.venue,
        "capacity": _clean(
            pairs.get("모집인원")
            or pairs.get("신청인원/모집정원")
            or pairs.get("정원/대기정원")
        )
        or listed.capacity,
        "fee": _clean(pairs.get("교육비") or pairs.get("수강료")) or listed.fee,
        "application_control": control,
        "application_url": application_url,
    }


def _semantic_rejection(listed: _ListedCourse) -> str:
    title = _clean(listed.title)
    if _TEST_TITLE_RE.fullmatch(title):
        return "test_record"
    if _MEDIA_AGGREGATE_RE.fullmatch(title) and (
        "교육별 상이" in listed.schedule or "교육별 상이" in listed.capacity
    ):
        return "aggregate_overview_duplicates_individual_media_courses"
    return ""


def _status(listed: _ListedCourse, audit_date: date) -> str:
    if audit_date < listed.apply_start:
        return "SCHEDULED"
    if listed.apply_start <= audit_date <= listed.apply_end:
        return "OPEN"
    return "CLOSED"


def _branch_for(listed: _ListedCourse) -> str:
    if listed.category_key == "lifelong":
        if listed.branch not in BUAN_LIFELONG_BRANCHES:
            raise BuanContractError(
                f"lifelong:{listed.identity}: missing reconciled official branch"
            )
        return listed.branch
    if listed.category_key == "culture":
        return "옹기종기문화센터"
    if listed.category_key == "arts":
        return "부안예술회관"
    if listed.category_key == "media":
        if listed.venue and not listed.venue.startswith("부안미디어센터"):
            raise BuanContractError(
                f"media:{listed.identity}: unexpected venue {listed.venue}"
            )
        return "부안미디어센터"
    raise BuanContractError(f"unknown category: {listed.category_key}")


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _row(
    target: Any,
    listed: _ListedCourse,
    detail: Mapping[str, Any],
    audit_date: date,
) -> dict[str, Any]:
    category = BUAN_CATEGORY_BY_KEY[listed.category_key]
    branch = _branch_for(listed)
    address = _clean(BUAN_BRANCH_ADDRESSES.get(branch))
    venue = _clean(detail.get("venue")) or listed.venue or branch
    fee = _clean(detail.get("fee"))
    if fee == "무료":
        fee = "0원"
    status = _status(listed, audit_date)
    control = bool(detail.get("application_control"))
    application_url = _clean(detail.get("application_url"))
    extra = _target_extra(target)
    output: dict[str, Any] = {
        "provider": BUAN_PROVIDER,
        "provider_course_id": (
            f"{BUAN_PROVIDER}:education:{listed.category_key}:{listed.identity}"
        ),
        "title": listed.title,
        "branch": branch,
        "branch_code": f"{BUAN_PROVIDER}:{listed.category_key}:{branch}",
        "preserve_branch": True,
        "branch_url": category.url,
        "raw_url": listed.detail_url,
        "application_url": application_url,
        "application_type": (
            "ONLINE_APPLICATION_CONTROL" if control else "INFO_ONLY_NO_HTML_CONTROL"
        ),
        "application_method_raw": (
            "온라인 신청" if control else "게시물 안내(HTML 신청 제어 없음)"
        ),
        "reservation_available": bool(control and status == "OPEN"),
        "status": status,
        "period": _clean(detail.get("period")) or listed.period,
        "apply_period": _clean(detail.get("apply_period")) or listed.apply_period,
        "schedule_raw": _clean(detail.get("schedule")) or listed.schedule,
        "start_date": listed.event_start.isoformat(),
        "end_date": listed.event_end.isoformat(),
        "apply_start_date": listed.apply_start.isoformat(),
        "apply_end_date": listed.apply_end.isoformat(),
        "target": _clean(detail.get("target")) or listed.target,
        "capacity": _clean(detail.get("capacity")) or listed.capacity,
        "fee": fee,
        "venue_name": venue,
        "room": venue,
        "address": address,
        "venue_address": address,
        "category": listed.category_label or category.label,
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "static_html+detail_html",
        "program_type": "교육",
        "municipality_code": BUAN_MUNICIPALITY_CODE,
        "municipality_name": BUAN_MUNICIPALITY_NAME,
        "municipality_full_name": BUAN_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": BUAN_PARSER,
            "identity": listed.identity,
            "category_key": listed.category_key,
            "source_page": listed.page,
            "raw_event_end": listed.raw_event_end.isoformat(),
            "event_end_corrected": listed.event_end != listed.raw_event_end,
            "branch_basis": (
                "reconciled official lifelong tab"
                if listed.category_key == "lifelong"
                else "active official education category and detail venue"
            ),
            "detail_verified": True,
            "application_control_verified": True,
        },
    }
    public_text = " ".join(
        _clean(output.get(key))
        for key in ("title", "branch", "target", "venue_name", "schedule_raw")
    )
    if _PHONE_RE.search(public_text) or _EMAIL_RE.search(public_text):
        raise BuanContractError(
            f"{listed.category_key}:{listed.identity}: public row leaked contact data"
        )
    return output


def collect_buan_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = BUAN_MAX_PAGES,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Buan integrated education snapshot."""

    audit_date = _today(today)
    factory = session_factory or _default_session_factory
    html_fetcher = fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": BUAN_MUNICIPALITY_CODE,
        "municipality_name": BUAN_MUNICIPALITY_NAME,
        "owner_provider": BUAN_PROVIDER,
        "canonical_url": BUAN_CANONICAL_URL,
        "candidate_id": BUAN_CANONICAL_CANDIDATE_ID,
        "parser": BUAN_PARSER,
        "ownership_scope": BUAN_OWNERSHIP_SCOPE,
        "cutoff": audit_date.isoformat(),
        "pages": 0,
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
        if not is_buan_education_target(target):
            raise BuanContractError("target is not the canonical Buan education owner")
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise BuanContractError("invalid collector limits")
        session = factory()

        root = _fetch_soup(session, BUAN_CANONICAL_URL, timeout, html_fetcher, meta)
        _validate_active_menu(root)

        category_rows: dict[str, list[_ListedCourse]] = {}
        category_pages: dict[str, int] = {}
        first_pages: dict[str, _Page] = {}
        for category in BUAN_CATEGORIES:
            rows, first, last_page = _collect_pages(
                session,
                category,
                timeout,
                max_pages,
                html_fetcher,
                meta,
            )
            category_rows[category.key] = rows
            category_pages[category.key] = last_page
            first_pages[category.key] = first

        lifetime_rows = category_rows["lifelong"]
        membership: dict[str, str] = {}
        branch_source_counts: dict[str, int] = {}
        for branch, _ in first_pages["lifelong"].branch_tabs:
            branch_rows, _, _ = _collect_pages(
                session,
                BUAN_CATEGORY_BY_KEY["lifelong"],
                timeout,
                max_pages,
                html_fetcher,
                meta,
                branch=branch,
            )
            branch_source_counts[branch] = len(branch_rows)
            for listed in branch_rows:
                if listed.identity in membership:
                    raise BuanContractError(
                        f"lifelong:{listed.identity}: appears in multiple branch ledgers"
                    )
                membership[listed.identity] = branch
        if set(membership) != {row.identity for row in lifetime_rows}:
            raise BuanContractError("lifelong branch ledgers do not equal the 전체 ledger")
        category_rows["lifelong"] = [
            replace(row, branch=membership[row.identity]) for row in lifetime_rows
        ]

        listed = [
            row
            for category in BUAN_CATEGORIES
            for row in category_rows[category.key]
        ]
        source_keys = [(row.category_key, row.identity) for row in listed]
        if len(source_keys) != len(set(source_keys)):
            raise BuanContractError("duplicate identities across active categories")

        raw_current = [row for row in listed if row.raw_event_end >= audit_date]
        corrected_current = [row for row in listed if row.event_end >= audit_date]
        if len(raw_current) > detail_limit:
            meta["source_cap_reached"] = True
            raise BuanContractError(
                "detail_limit would create a partial current/future snapshot"
            )

        detail_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for row in raw_current:
            soup = _fetch_soup(
                session, row.detail_url, timeout, html_fetcher, meta
            )
            detail_by_key[(row.category_key, row.identity)] = _parse_detail(soup, row)
            meta["detail_pages"] = int(meta["detail_pages"]) + 1

        semantic_reasons = Counter(
            reason for row in listed if (reason := _semantic_rejection(row))
        )
        semantic_samples = [
            row.title for row in listed if _semantic_rejection(row)
        ][:10]
        accepted_current = [
            row for row in corrected_current if not _semantic_rejection(row)
        ]
        output = [
            _row(
                target,
                row,
                detail_by_key[(row.category_key, row.identity)],
                audit_date,
            )
            for row in accepted_current
        ]
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != len(accepted_current):
            raise BuanContractError("dedupe changed an already-unique current snapshot")

        correction_count = sum(row.raw_event_end != row.event_end for row in listed)
        status_counts = dict(Counter(_clean(row.get("status")) for row in output))
        branch_counts = dict(Counter(_clean(row.get("branch")) for row in output))
        category_current_counts = dict(
            Counter(
                BUAN_CATEGORY_BY_KEY[row.category_key].label
                for row in accepted_current
            )
        )
        application_controls = sum(
            bool(row.get("application_url"))
            or row.get("application_type") == "ONLINE_APPLICATION_CONTROL"
            for row in output
        )
        category_counts = {
            BUAN_CATEGORY_BY_KEY[key].label: len(value)
            for key, value in category_rows.items()
        }
        meta.update(
            {
                "pages": sum(category_pages.values()),
                "data_pages": sum(category_pages.values()),
                "list_requests": int(meta["logical_requests"]) - int(meta["detail_pages"]),
                "category_pages": {
                    BUAN_CATEGORY_BY_KEY[key].label: value
                    for key, value in category_pages.items()
                },
                "category_counts": category_counts,
                "category_current_counts": category_current_counts,
                "branch_source_counts": branch_source_counts,
                "official_branch_addresses": dict(BUAN_BRANCH_ADDRESSES),
                "active_menu_labels": list(BUAN_ACTIVE_MENU_LABELS),
                "source_rows": len(listed),
                "source_total": len(listed),
                "raw_current_source_count": len(raw_current),
                "current_source_count": len(corrected_current),
                "expired_source_count": len(listed) - len(corrected_current),
                "source_date_correction_count": correction_count,
                "source_date_corrections": [
                    {
                        "category_key": item.category_key,
                        "identity": item.identity,
                        "source_end": item.source_end.isoformat(),
                        "corrected_end": item.corrected_end.isoformat(),
                        "evidence": item.evidence,
                    }
                    for item in BUAN_SOURCE_CORRECTIONS
                    if (item.category_key, item.identity) in set(source_keys)
                ],
                "semantic_rejected_row_count": sum(semantic_reasons.values()),
                "semantic_accepted_source_count": len(listed)
                - sum(semantic_reasons.values()),
                "semantic_rejection_reasons": dict(semantic_reasons),
                "semantic_rejected_title_samples": semantic_samples,
                "semantic_rejected_current_count": len(corrected_current)
                - len(accepted_current),
                "current_status_counts": status_counts,
                "source_status_counts": status_counts,
                "branch_counts": branch_counts,
                "application_control_count": application_controls,
                "info_only_count": len(output) - application_controls,
                "detail_attempts": len(raw_current),
                "detail_verified": len(detail_by_key),
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
                    "활성 교육 메뉴의 현재·향후 개별 과정이 없음" if not output else ""
                ),
                "excluded_owner_boundaries": dict(BUAN_EXCLUDED_OWNER_BOUNDARIES),
                "portal_candidate_relationship": (
                    "separate_lifelong_notice_owner_with_partial_announcement_overlap"
                ),
            }
        )
        return output, BUAN_PARSER, meta
    except Exception as exc:  # every network/parser/contract problem fails closed
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
        return [], BUAN_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_buan_education


__all__ = [
    "BUAN_ACTIVE_MENU_LABELS",
    "BUAN_BRANCH_ADDRESSES",
    "BUAN_CANONICAL_CANDIDATE_ID",
    "BUAN_CANONICAL_URL",
    "BUAN_CANDIDATE_DECISIONS",
    "BUAN_CATEGORIES",
    "BUAN_DISCOVERY_PLAYGROUND_CANDIDATE_ID",
    "BUAN_EXCLUDED_OWNER_BOUNDARIES",
    "BUAN_FETCH_ATTEMPTS",
    "BUAN_HOST",
    "BUAN_LIFELONG_BRANCHES",
    "BUAN_MAX_PAGES",
    "BUAN_MUNICIPALITY_CODE",
    "BUAN_MUNICIPALITY_NAME",
    "BUAN_OWNERSHIP_SCOPE",
    "BUAN_PAGE_SIZE",
    "BUAN_PARSER",
    "BUAN_PORTAL_CANDIDATE_ID",
    "BUAN_PORTAL_PROVIDER",
    "BUAN_PORTAL_URL",
    "BUAN_PROVIDER",
    "BUAN_SOURCE_CORRECTIONS",
    "BuanContractError",
    "collect",
    "collect_buan_education",
    "is_buan_education_target",
    "is_buan_lifelong_notice_target",
    "is_target",
]
