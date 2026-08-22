"""Fail-closed collector for Buyeo-gun Lifelong Learning Center courses.

The reviewed discovery URL is only the Buyeo education-site homepage.  The
canonical reservation ledger lives at ``/_prog/lll_edu/`` and has two nested
levels: five pages of programme groups followed by one or more pages of
individual courses for every group.  The server clamps requests after the
last page to the exact final page instead of returning an empty page.

This collector traverses every group and every individual-course page,
proves each immediate clamp, and rechecks the first/final boundaries.  The
application endpoint and downloadable lesson plans are never requested.
Instructor names are read only to prove the public card schema and are then
discarded; returned rows use a strict non-PII allowlist.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Comment
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BUYEO_PROVIDER = "MUNI_EDU_BUYEO_GO_KR_8DF02931"
BUYEO_HOMEPAGE_DERIVED_PROVIDER = BUYEO_PROVIDER
BUYEO_CANONICAL_DERIVED_PROVIDER = "MUNI_EDU_BUYEO_GO_KR_24450708"
BUYEO_HOMEPAGE_CANDIDATE_ID = "MUNI_IR_E34BB4693437"
BUYEO_CANONICAL_CANDIDATE_ID = "MUNI_IR_4951139821FD"
BUYEO_DIRECTORY_CANDIDATE_ID = "MUNI_IR_3B8BE2A914B1"

BUYEO_MUNICIPALITY_CODE = "4476000000"
BUYEO_MUNICIPALITY_NAME = "충청남도 부여군"
BUYEO_HOST = "edu.buyeo.go.kr"
BUYEO_LIST_PATH = "/_prog/lll_edu/"
BUYEO_LIST_ACTION_PATH = "/_prog/lll_edu/index.php"
BUYEO_APPLICATION_PATH = "/_prog/lll_edu_app/"
BUYEO_DOWNLOAD_PATH = "/_prog/download_lll.php"
BUYEO_CANONICAL_URL = f"https://{BUYEO_HOST}{BUYEO_LIST_PATH}"
BUYEO_HOMEPAGE_URL = f"https://{BUYEO_HOST}/html/kr/"
BUYEO_DIRECTORY_URL = f"https://{BUYEO_HOST}/_prog/lll_orgedu/"
BUYEO_NATIONAL_MUSEUM_URL = "https://modu.museum.go.kr/learn?museum=6"

BUYEO_SITE_NAME = "부여군 평생학습관"
BUYEO_OFFICIAL_BRANCH = "부여군 평생학습관"
BUYEO_OFFICIAL_ADDRESS = "충청남도 부여군 부여읍 성왕로 360"
BUYEO_OFFICIAL_FOOTER = "[33159] 충남 부여군 부여읍 성왕로 360 부여군 평생학습관"
BUYEO_OUTER_PAGE_SIZE = 10
BUYEO_COURSE_PAGE_SIZE = 10
BUYEO_MAX_HTML_BYTES = 3_000_000
BUYEO_FETCH_ATTEMPTS = 2
BUYEO_PARSER = (
    "buyeo_lifelong_complete_nested_ledger+advertised_outer_and_group_pages+"
    "exact_clamped_sentinels+stable_first_last_boundaries+all_course_cards+"
    "identity_bound_visible_application_controls+no_application_fetch+pii_allowlist"
)
BUYEO_OWNERSHIP_SCOPE = "official_buyeo_lifelong_learning_regular_courses"

BUYEO_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, str]] = {
    "canonical_online_application_ledger": {
        "url": BUYEO_CANONICAL_URL,
        "decision": "include_as_complete_buyeo_lifelong_course_owner",
    },
    "reviewed_homepage": {
        "url": BUYEO_HOMEPAGE_URL,
        "decision": "exclude_discovery_homepage_alias_and_retarget_provider_to_canonical_ledger",
    },
    "course_search_directory": {
        "url": BUYEO_DIRECTORY_URL,
        "decision": "exclude_stale_information_directory_alias_of_canonical_course_archive",
    },
    "national_buyeo_museum": {
        "url": BUYEO_NATIONAL_MUSEUM_URL,
        "decision": "exclude_separate_national_museum_owner_already_collected",
    },
}

BUYEO_PII_FIELDS_NEVER_PERSISTED = (
    "강사",
    "전화",
    "이메일",
    "신청자",
    "첨부파일 본문",
)


SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class BuyeoContractError(ValueError):
    """Raised when the audited Buyeo public contract changes."""


_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d{0,11}$")
_TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2}")
_COURSE_TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{1,2}\s*~\s*\d{1,2}:\d{1,2}")
_INTEGER_RE = re.compile(r"^\d[\d,]*$")
_COUNT_PAIR_RE = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_KOREAN_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
)
_KOREAN_PARTIAL_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?:\s*\.)?"
)
_NUMERIC_PARTIAL_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})(?:\s*\.)?"
)
_APPLICATION_HINT_RE = re.compile(r"/_prog/lll_edu_app/\?mng_no=([1-9]\d{0,11})")
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CANCELLED_RE = re.compile(r"(?:^|[\[<(])\s*(?:취소|폐강)\s*(?:$|[\])>])")

_OUTER_HEADERS = ("교육과정명", "접수기간", "접수시간", "선정방법", "접수상태", "강좌")
_SUMMARY_LABELS = ("교육과정명", "접수기간", "접수시간", "유형", "접수상태")
_COURSE_FIELDS_WITH_COUNTS = frozenset(
    {"교육기간", "교육시간", "대상", "강사", "수강인원", "접수인원/최대모집인원"}
)
_COURSE_FIELDS_WITH_MAXIMUM = frozenset(
    {"교육기간", "교육시간", "대상", "강사", "수강인원", "최대모집인원"}
)
_PROGRAM_STATUSES = frozenset({"접수대기", "접수중", "접수마감"})
_COURSE_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "폐쇄": "CLOSED",
}
_SELECTION_METHODS = frozenset({"추첨", "선착순", "방문접수"})
_SESSION_LABELS = frozenset({"주간", "야간", "자격증"})

_KNOWN_UNDATED_HISTORICAL = {
    (
        "7",
        "171",
        "2020년 지속가능 발전 강좌 원데이클래스 수강생 모집",
        "강의계획서 참고",
    )
}
_KNOWN_EMPTY_HISTORICAL_INSTRUCTORS = {
    ("37", "492", "2023 학부모 입시 아카데미"),
    ("31", "392", "2022 학부모 입시 아카데미"),
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _strict_target_url(value: Any) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == BUYEO_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BUYEO_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def is_buyeo_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == BUYEO_PROVIDER
        and _strict_target_url(_target_value(target, "url"))
    )


is_target = is_buyeo_education_target


def buyeo_outer_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return BUYEO_CANONICAL_URL
    return BUYEO_CANONICAL_URL + "?" + urlencode(
        (("site_dvs_cd", "lll"), ("menu_dvs_cd", "0201"), ("GotoPage", str(page)))
    )


def buyeo_group_url(group_id: Any, page: int = 1) -> str:
    identity = _clean(group_id)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("invalid Buyeo programme identity")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    query: list[tuple[str, str]] = [
        ("mode", "SL"),
        ("p_mng_no", identity),
        ("site_dvs_cd", "lll"),
        ("menu_dvs_cd", "0201"),
    ]
    if page > 1:
        query.extend((("skey", ""), ("sval", ""), ("GotoPage", str(page))))
    return BUYEO_CANONICAL_URL + "?" + urlencode(query)


def buyeo_application_url(course_id: Any) -> str:
    identity = _clean(course_id)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("invalid Buyeo course identity")
    return f"https://{BUYEO_HOST}{BUYEO_APPLICATION_PATH}?" + urlencode(
        (("mng_no", identity),)
    )


def buyeo_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    # Connection-only retries are safe; HTTP/status retries are counted by
    # this module's explicit fail-closed request loop.
    retry = Retry(
        total=0,
        connect=2,
        read=0,
        status=0,
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    current.mount("https://", HTTPAdapter(max_retries=retry))
    return current


def _response_soup(value: Any, expected_url: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        content = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    else:
        try:
            status = int(getattr(value, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        if status != 200:
            raise BuyeoContractError(f"unexpected HTTP status {status}")
        if getattr(value, "history", ()):
            raise BuyeoContractError("HTTP redirects are not accepted")
        response_url = _clean(getattr(value, "url", expected_url))
        if response_url and response_url != expected_url:
            raise BuyeoContractError("response URL changed")
        headers = getattr(value, "headers", {}) or {}
        content_type = _clean(headers.get("Content-Type") if isinstance(headers, Mapping) else "")
        if content_type and "html" not in content_type.casefold():
            raise BuyeoContractError("response is not HTML")
        raw = getattr(value, "content", None)
        if isinstance(raw, bytes):
            content = raw
        else:
            content = str(getattr(value, "text", "") or "").encode("utf-8")
    if not content or len(content) > BUYEO_MAX_HTML_BYTES:
        raise BuyeoContractError("HTML response size is invalid")
    try:
        html = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BuyeoContractError("HTML is not valid UTF-8") from exc
    return BeautifulSoup(html, "lxml")


def _request_soup(
    current: Any,
    url: str,
    timeout: int,
    fetcher: Optional[HtmlFetcher],
    meta: dict[str, Any],
    request_kind: str,
) -> BeautifulSoup:
    meta[request_kind] = int(meta.get(request_kind) or 0) + 1
    meta["logical_requests"] = int(meta.get("logical_requests") or 0) + 1
    messages: list[str] = []
    for attempt in range(1, BUYEO_FETCH_ATTEMPTS + 1):
        meta["physical_requests"] = int(meta.get("physical_requests") or 0) + 1
        try:
            response = (
                fetcher(current, url, timeout)
                if fetcher is not None
                else current.get(url, timeout=timeout, allow_redirects=False)
            )
            return _response_soup(response, url)
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    meta["request_retry_count"] = int(meta["physical_requests"]) - int(meta["logical_requests"])
    raise BuyeoContractError("; ".join(messages))


def _site_contract(soup: BeautifulSoup) -> None:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if title != "온라인 수강신청 > 정규강좌 > 부여평생학습관":
        raise BuyeoContractError("official page title changed")
    headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h1, h2")]
    if BUYEO_SITE_NAME not in headings:
        raise BuyeoContractError("official site heading changed")
    if BUYEO_OFFICIAL_FOOTER not in _clean(soup.get_text(" ", strip=True)):
        raise BuyeoContractError("official branch/address footer changed")


def _selected_option(select: Any) -> str:
    selected = select.select("option[selected]")
    if len(selected) > 1:
        raise BuyeoContractError("multiple selected filter options")
    node = selected[0] if selected else select.select_one("option")
    return _clean(node.get("value") if node else "")


def _outer_form_contract(soup: BeautifulSoup) -> None:
    matches = [
        form
        for form in soup.select("form")
        if urlparse(urljoin(BUYEO_CANONICAL_URL, _clean(form.get("action")))).path
        == BUYEO_LIST_ACTION_PATH
    ]
    if len(matches) != 1:
        raise BuyeoContractError("expected one official course search form")
    form = matches[0]
    if _clean(form.get("method")).casefold() != "get":
        raise BuyeoContractError("course search form method changed")
    action = urlparse(urljoin(BUYEO_CANONICAL_URL, _clean(form.get("action"))))
    if action.scheme != "https" or action.hostname != BUYEO_HOST or action.query or action.fragment:
        raise BuyeoContractError("course search form action changed")
    for name, expected in (("site_dvs_cd", "lll"), ("menu_dvs_cd", "0201")):
        nodes = form.select(f'input[name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            raise BuyeoContractError(f"course search field {name} changed")
    statuses = form.select('select[name="sch_status"]')
    if len(statuses) != 1 or _selected_option(statuses[0]):
        raise BuyeoContractError("course search status is not unfiltered")
    actual_statuses = {
        _clean(option.get("value")): _clean(option.get_text(" ", strip=True))
        for option in statuses[0].select("option[value]")
    }
    if actual_statuses != {"": "전체", "1": "접수대기", "2": "접수중", "3": "접수마감"}:
        raise BuyeoContractError("course search status vocabulary changed")
    skey = form.select('select[name="skey"] option')
    if len(skey) != 1 or _clean(skey[0].get("value")) != "title":
        raise BuyeoContractError("course search key changed")
    sval = form.select('input[name="sval"]')
    if len(sval) != 1 or _clean(sval[0].get("value")):
        raise BuyeoContractError("course search text is not empty")


def _pager_contract(soup: BeautifulSoup, expected_display: int, known_last: int = 0) -> int:
    pagers = soup.select("div.page_navi")
    if len(pagers) != 1:
        raise BuyeoContractError("expected one pagination block")
    pager = pagers[0]
    current = pager.select(":scope > span.on")
    if len(current) != 1 or _clean(current[0].get_text(" ", strip=True)) != str(expected_display):
        raise BuyeoContractError("displayed page number changed")
    values: list[int] = []
    for node in pager.select(":scope > a[href], :scope > span.on"):
        text = _clean(node.get_text(" ", strip=True))
        if not text.isdigit():
            raise BuyeoContractError("unexpected pagination control")
        values.append(int(text))
    if len(values) != len(set(values)) or not values:
        raise BuyeoContractError("pagination numbers are duplicated/empty")
    last = max(values)
    if set(values) != set(range(1, last + 1)):
        raise BuyeoContractError("pagination sequence is incomplete")
    if known_last and last != known_last:
        raise BuyeoContractError("advertised final page changed")
    return last


def _make_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise BuyeoContractError("invalid official course date") from exc


def _full_date(value: str) -> Optional[date]:
    matches = list(_KOREAN_DATE_RE.finditer(value)) + list(_NUMERIC_DATE_RE.finditer(value))
    if not matches:
        return None
    matches.sort(key=lambda match: match.start())
    year, month, day = matches[0].groups()
    return _make_date(int(year), int(month), int(day))


def _partial_date(value: str, year: int) -> Optional[date]:
    matches = list(_KOREAN_PARTIAL_DATE_RE.finditer(value)) + list(
        _NUMERIC_PARTIAL_DATE_RE.finditer(value)
    )
    if not matches:
        return None
    matches.sort(key=lambda match: match.start())
    month, day = matches[0].groups()
    return _make_date(year, int(month), int(day))


def _parse_period(value: Any) -> Optional[tuple[date, date]]:
    text = _clean(value)
    if text == "강의계획서 참고":
        return None
    left, separator, right = text.partition("~")
    if not separator:
        raise BuyeoContractError("course period lacks a range separator")
    start = _full_date(left)
    if start is None:
        raise BuyeoContractError("course period start date changed")
    end = _full_date(right) or _partial_date(right, start.year)
    if end is None:
        raise BuyeoContractError("course period end date changed")
    if end < start and end.month < start.month:
        end = _make_date(start.year + 1, end.month, end.day)
    if end < start:
        raise BuyeoContractError("course period ends before it starts")
    return start, end


def _normalize_period(period: tuple[date, date]) -> str:
    return f"{period[0].isoformat()} ~ {period[1].isoformat()}"


def _strict_internal_href(value: Any, *, path: str) -> tuple[str, dict[str, list[str]]]:
    absolute = urljoin(BUYEO_CANONICAL_URL, _clean(value))
    parsed = urlparse(absolute)
    try:
        port = parsed.port
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise BuyeoContractError("malformed official URL") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == BUYEO_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and not parsed.fragment
    ):
        raise BuyeoContractError("official URL escaped its audited path")
    return absolute, query


def _parse_outer_page(
    soup: BeautifulSoup,
    requested_page: int,
    expected_display: int,
    known_last: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    _site_contract(soup)
    _outer_form_contract(soup)
    last = _pager_contract(soup, expected_display, known_last)
    tables = []
    for table in soup.select("table"):
        headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
        if headers == _OUTER_HEADERS:
            tables.append(table)
    if len(tables) != 1:
        raise BuyeoContractError("expected one official programme table")
    rows: list[dict[str, Any]] = []
    for row in tables[0].select("tbody > tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 6:
            raise BuyeoContractError("programme row schema changed")
        values = tuple(_clean(cell.get_text(" ", strip=True)) for cell in cells)
        links = cells[5].select("a[href]")
        if len(links) != 1 or _clean(links[0].get_text(" ", strip=True)) != "강좌상세보기":
            raise BuyeoContractError("programme detail control changed")
        detail_url, query = _strict_internal_href(links[0].get("href"), path=BUYEO_LIST_PATH)
        if set(query) != {"mode", "p_mng_no", "site_dvs_cd", "menu_dvs_cd"}:
            raise BuyeoContractError("programme detail query changed")
        if query.get("mode") != ["SL"] or query.get("site_dvs_cd") != ["lll"] or query.get(
            "menu_dvs_cd"
        ) != ["0201"]:
            raise BuyeoContractError("programme detail scope changed")
        identity = _clean((query.get("p_mng_no") or [""])[0])
        if not _IDENTITY_RE.fullmatch(identity):
            raise BuyeoContractError("programme identity changed")
        if not values[0] or values[3] not in _SELECTION_METHODS or values[4] not in _PROGRAM_STATUSES:
            raise BuyeoContractError("programme title/method/status changed")
        apply_period = _parse_period(values[1])
        if apply_period is None or not _TIME_RANGE_RE.fullmatch(values[2]):
            raise BuyeoContractError("programme application period/time changed")
        rows.append(
            {
                "group_id": identity,
                "title": values[0],
                "apply_period": _normalize_period(apply_period),
                "apply_start": apply_period[0],
                "apply_end": apply_period[1],
                "apply_time": values[2],
                "selection_method": values[3],
                "source_status": values[4],
                "detail_url": detail_url,
                "outer_page": expected_display,
            }
        )
    if len(rows) > BUYEO_OUTER_PAGE_SIZE:
        raise BuyeoContractError("programme page size exceeded")
    return rows, last


def _outer_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            row["group_id"],
            row["title"],
            row["apply_period"],
            row["apply_time"],
            row["selection_method"],
            row["source_status"],
        )
        for row in rows
    )


def _group_form_contract(soup: BeautifulSoup, parent: Mapping[str, Any]) -> None:
    forms = [
        form
        for form in soup.select("form")
        if urlparse(urljoin(BUYEO_CANONICAL_URL, _clean(form.get("action")))).path
        == BUYEO_LIST_ACTION_PATH
        and form.select_one('input[name="mode"]') is not None
    ]
    if len(forms) != 1 or _clean(forms[0].get("method")).casefold() != "post":
        raise BuyeoContractError("programme course form changed")
    form = forms[0]
    expected = {
        "mode": "SL",
        "site_dvs_cd": "lll",
        "menu_dvs_cd": "0201",
        "p_mng_no": _clean(parent["group_id"]),
    }
    for name, value in expected.items():
        nodes = form.select(f'input[name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != value:
            raise BuyeoContractError(f"programme course field {name} changed")


def _summary_contract(soup: BeautifulSoup, parent: Mapping[str, Any]) -> None:
    candidates: list[dict[str, str]] = []
    for table in soup.select("table"):
        pairs: dict[str, str] = {}
        for row in table.select("tbody > tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) != 2 or cells[0].name != "th" or cells[1].name != "td":
                continue
            pairs[_clean(cells[0].get_text(" ", strip=True))] = _clean(
                cells[1].get_text(" ", strip=True)
            )
        if tuple(pairs) == _SUMMARY_LABELS:
            candidates.append(pairs)
    if len(candidates) != 1:
        raise BuyeoContractError("expected one programme summary table")
    summary = candidates[0]
    expected = {
        "교육과정명": parent["title"],
        "접수기간": parent["apply_period"],
        "접수시간": parent["apply_time"],
        "유형": parent["selection_method"],
        "접수상태": parent["source_status"],
    }
    actual_period = _parse_period(summary["접수기간"])
    if actual_period is None:
        raise BuyeoContractError("programme summary application period missing")
    summary = {**summary, "접수기간": _normalize_period(actual_period)}
    if summary != expected:
        raise BuyeoContractError("programme summary differs from catalogue row")


def _field_pairs(card: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in card.select(".item li"):
        label_node = item.select_one("b")
        if label_node is None:
            raise BuyeoContractError("course card field label missing")
        prefix = _clean(label_node.get_text(" ", strip=True))
        label = prefix.rstrip(":").strip()
        whole = _clean(item.get_text(" ", strip=True))
        if not whole.startswith(prefix) or label in result:
            raise BuyeoContractError("course card field structure changed")
        result[label] = _clean(whole[len(prefix) :])
    if frozenset(result) not in {_COURSE_FIELDS_WITH_COUNTS, _COURSE_FIELDS_WITH_MAXIMUM}:
        raise BuyeoContractError("course card labels changed")
    return result


def _parse_integer(value: Any, label: str) -> int:
    text = _clean(value)
    if not _INTEGER_RE.fullmatch(text):
        raise BuyeoContractError(f"course {label} is not an integer")
    return int(text.replace(",", ""))


def _visible_application_controls(card: Any, identity: str) -> list[str]:
    result: list[str] = []
    for anchor in card.select("a[href]"):
        absolute = urljoin(BUYEO_CANONICAL_URL, _clean(anchor.get("href")))
        if urlparse(absolute).path != BUYEO_APPLICATION_PATH:
            continue
        application_url, query = _strict_internal_href(anchor.get("href"), path=BUYEO_APPLICATION_PATH)
        if set(query) != {"mng_no"} or query.get("mng_no") != [identity]:
            raise BuyeoContractError("application control is not identity-bound")
        if _clean(anchor.get_text(" ", strip=True)) != "신청하기":
            raise BuyeoContractError("application control label changed")
        result.append(application_url)
    if len(result) > 1:
        raise BuyeoContractError("multiple visible application controls")
    return result


def _parse_course_card(card: Any, parent: Mapping[str, Any], page: int) -> dict[str, Any]:
    direct_statuses = card.select(":scope > span[class^='tag']")
    direct_sessions = card.select(":scope > span[class^='gu']")
    if len(direct_statuses) != 1 or len(direct_sessions) != 1:
        raise BuyeoContractError("course status/session badges changed")
    source_status = _clean(direct_statuses[0].get_text(" ", strip=True))
    session_label = _clean(direct_sessions[0].get_text(" ", strip=True))
    if source_status not in _COURSE_STATUS_MAP or session_label not in _SESSION_LABELS:
        raise BuyeoContractError("unknown course status/session badge")

    headings = card.select(".txtwrap > strong.h-box")
    if len(headings) != 2:
        raise BuyeoContractError("course title/category structure changed")
    category = _clean(headings[0].get_text(" ", strip=True))
    if not (category.startswith("[") and category.endswith("]")):
        raise BuyeoContractError("course category wrapper changed")
    category = category[1:-1].strip()
    title = _clean(headings[1].get_text(" ", strip=True))
    if category != parent["title"] or len(title) < 2:
        raise BuyeoContractError("course category/title identity changed")

    fields = _field_pairs(card)
    comments = "\n".join(
        str(node) for node in card.find_all(string=lambda value: isinstance(value, Comment))
    )
    hinted_ids = set(_APPLICATION_HINT_RE.findall(comments))
    visible_candidates: list[str] = []
    for anchor in card.select("a[href]"):
        absolute = urljoin(BUYEO_CANONICAL_URL, _clean(anchor.get("href")))
        if urlparse(absolute).path == BUYEO_APPLICATION_PATH:
            query = parse_qs(urlparse(absolute).query, keep_blank_values=True)
            visible_candidates.extend(query.get("mng_no") or [])
    identities = hinted_ids | set(visible_candidates)
    if len(identities) != 1:
        raise BuyeoContractError("course application identity hint changed")
    identity = next(iter(identities))
    if not _IDENTITY_RE.fullmatch(identity):
        raise BuyeoContractError("course identity is invalid")
    controls = _visible_application_controls(card, identity)
    if source_status == "접수중" and len(controls) != 1:
        raise BuyeoContractError("open course lacks one visible application control")
    if source_status != "접수중" and controls:
        raise BuyeoContractError("inactive course exposes an application control")

    period_text = fields["교육기간"]
    parsed_period = _parse_period(period_text)
    if parsed_period is None:
        known = (parent["group_id"], identity, title, period_text)
        if known not in _KNOWN_UNDATED_HISTORICAL or parent["apply_end"].year != 2020:
            raise BuyeoContractError("unknown course has no auditable education period")
    schedule = _clean(fields["교육시간"])
    if (
        not schedule
        or len(schedule) > 100
        or (
            not _COURSE_TIME_RANGE_RE.search(schedule)
            and schedule not in {"상시학습", "상시", "온라인"}
        )
    ):
        raise BuyeoContractError("course education time changed")
    target = _clean(fields["대상"])
    instructor = _clean(fields["강사"])
    if not target:
        raise BuyeoContractError("course target/instructor schema changed")
    if not instructor and (parent["group_id"], identity, title) not in _KNOWN_EMPTY_HISTORICAL_INSTRUCTORS:
        raise BuyeoContractError("unknown course has an empty instructor field")
    class_size = _parse_integer(fields["수강인원"], "class size")
    if class_size <= 0:
        raise BuyeoContractError("course class size must be positive")
    applicants: Optional[int] = None
    if "접수인원/최대모집인원" in fields:
        match = _COUNT_PAIR_RE.fullmatch(fields["접수인원/최대모집인원"])
        if not match:
            raise BuyeoContractError("course applicant/capacity pair changed")
        applicants, capacity = (int(value.replace(",", "")) for value in match.groups())
    else:
        capacity = _parse_integer(fields["최대모집인원"], "maximum capacity")
    if capacity <= 0 or (applicants is not None and applicants < 0):
        raise BuyeoContractError("course capacity values are invalid")
    return {
        "group_id": _clean(parent["group_id"]),
        "course_id": identity,
        "title": title,
        "category": category,
        "source_status": source_status,
        "session_label": session_label,
        "period_text": period_text,
        "period": parsed_period,
        "schedule": schedule,
        "target": target,
        "instructor": instructor,
        "class_size": class_size,
        "capacity": capacity,
        "applicants": applicants,
        "application_url": controls[0] if controls else "",
        "archived_identity_hint": bool(hinted_ids),
        "detail_page": page,
        "parent": parent,
    }


def _parse_group_page(
    soup: BeautifulSoup,
    parent: Mapping[str, Any],
    requested_page: int,
    expected_display: int,
    known_last: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    _site_contract(soup)
    _group_form_contract(soup, parent)
    _summary_contract(soup, parent)
    last = _pager_contract(soup, expected_display, known_last)
    cards = soup.select("div.ui.ui-topbox.type2")
    parsed = [_parse_course_card(card, parent, expected_display) for card in cards]
    if len(parsed) > BUYEO_COURSE_PAGE_SIZE:
        raise BuyeoContractError("individual-course page size exceeded")
    if not parsed:
        raise BuyeoContractError("programme detail page has no courses")
    return parsed, last


def _course_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            row["group_id"],
            row["course_id"],
            row["title"],
            row["source_status"],
            row["session_label"],
            row["period_text"],
            row["schedule"],
            row["target"],
            row["class_size"],
            row["capacity"],
            row["applicants"],
            row["application_url"],
        )
        for row in rows
    )


def _normalized_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).casefold())


def _output_row(course: Mapping[str, Any]) -> dict[str, Any]:
    parent = course["parent"]
    period = course["period"]
    if period is None:
        raise BuyeoContractError("historical undated course cannot become current output")
    source_status = _clean(course["source_status"])
    application_url = _clean(course["application_url"])
    row = {
        "provider": BUYEO_PROVIDER,
        "provider_course_id": f"{BUYEO_PROVIDER}:lll-edu:{course['course_id']}",
        "title": _clean(course["title"]),
        "branch": BUYEO_OFFICIAL_BRANCH,
        "organizer": BUYEO_OFFICIAL_BRANCH,
        "provider_organizer": BUYEO_OFFICIAL_BRANCH,
        "venue_name": BUYEO_OFFICIAL_BRANCH,
        "address": BUYEO_OFFICIAL_ADDRESS,
        "start_date": period[0].isoformat(),
        "end_date": period[1].isoformat(),
        "period": _normalize_period(period),
        "schedule_raw": _clean(course["schedule"]),
        "apply_start": parent["apply_start"].isoformat(),
        "apply_end": parent["apply_end"].isoformat(),
        "apply_period": _clean(parent["apply_period"]),
        "status": _COURSE_STATUS_MAP[source_status],
        "raw_status": source_status,
        "reservation_available": bool(application_url),
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED" if application_url else "INFO_ONLY"
        ),
        "application_url": application_url,
        "raw_url": buyeo_group_url(parent["group_id"], int(course["detail_page"])),
        "target": _clean(course["target"]),
        "capacity": int(course["capacity"]),
        "current_applicants": course["applicants"],
        "municipality_code": BUYEO_MUNICIPALITY_CODE,
        "municipality_full_name": BUYEO_MUNICIPALITY_NAME,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "raw_fields": {
            "source_group_id": _clean(parent["group_id"]),
            "source_course_id": _clean(course["course_id"]),
            "source_group_title": _clean(parent["title"]),
            "source_status": source_status,
            "source_session": _clean(course["session_label"]),
            "source_selection_method": _clean(parent["selection_method"]),
            "source_application_time": _clean(parent["apply_time"]),
            "source_class_size": int(course["class_size"]),
            "source_archived_identity_hint_verified": bool(course["archived_identity_hint"]),
            "detail_verified": True,
            "application_control_verified": True,
            "instructor_discarded": True,
        },
    }
    if course["instructor"] and _clean(course["instructor"]) in repr(row):
        raise BuyeoContractError("instructor value escaped the output allowlist")
    return row


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _privacy_violations(rows: Iterable[Mapping[str, Any]]) -> int:
    forbidden = {"instructor", "teacher", "phone", "email", "contact", "applicant", "강사"}
    count = 0
    for row in rows:
        count += sum(key in row for key in forbidden)
        raw = row.get("raw_fields")
        if isinstance(raw, Mapping):
            count += sum(key in raw for key in forbidden)
        text = repr(row)
        count += len(_PHONE_RE.findall(text)) + len(_EMAIL_RE.findall(text))
    return count


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "detail_requests": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "source_total": 0,
        "source_rows": 0,
        "group_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "expired_count": 0,
        "undated_historical_count": 0,
        "cancelled_count": 0,
        "detail_attempts": 0,
        "detail_verified": 0,
        "application_control_count": 0,
        "application_endpoint_requests": 0,
        "download_endpoint_requests": 0,
        "duplicate_source_id_count": 0,
        "semantic_duplicate_count": 0,
        "privacy_violations": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": BUYEO_MUNICIPALITY_CODE,
        "municipality_name": BUYEO_MUNICIPALITY_NAME,
        "ownership_scope": BUYEO_OWNERSHIP_SCOPE,
        "candidate_ids": {
            "reviewed_homepage": BUYEO_HOMEPAGE_CANDIDATE_ID,
            "canonical_ledger": BUYEO_CANONICAL_CANDIDATE_ID,
            "course_search_directory": BUYEO_DIRECTORY_CANDIDATE_ID,
        },
        "owner_boundary_audit": {key: dict(value) for key, value in BUYEO_OWNER_BOUNDARY_AUDIT.items()},
    }


def collect_buyeo_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 700,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[HtmlFetcher] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete current/future Buyeo lifelong-course snapshot."""

    meta = _base_meta()
    if not is_buyeo_education_target(target):
        meta["configured_collection_error"] = "target does not match the canonical Buyeo education route"
        return [], BUYEO_PARSER, meta
    try:
        request_timeout = int(timeout)
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        cutoff = _audit_date(today)
        if request_timeout < 1 or allowed_pages < 1 or allowed_details < 0:
            raise ValueError
    except (TypeError, ValueError):
        meta["configured_collection_error"] = "timeout/max_pages/detail_limit/today are invalid"
        return [], BUYEO_PARSER, meta

    current = None
    errors: list[str] = []
    programmes: list[dict[str, Any]] = []
    courses: list[dict[str, Any]] = []
    outer_last = 0
    group_page_counts: dict[str, list[int]] = {}
    group_boundary_rechecks = 0
    try:
        current = (session_factory or buyeo_session_factory)()

        first = _request_soup(
            current, buyeo_outer_url(1), request_timeout, fetcher, meta, "list_requests"
        )
        first_rows, outer_last = _parse_outer_page(first, 1, 1)
        required_outer_requests = outer_last + 1 + len(set((1, outer_last)))
        meta["required_list_requests"] = required_outer_requests
        if required_outer_requests > allowed_pages:
            meta["source_cap_reached"] = True
            raise BuyeoContractError(
                f"max_pages cap allows {allowed_pages} of {required_outer_requests} required catalogue requests"
            )
        outer_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
        for page in range(2, outer_last + 1):
            soup = _request_soup(
                current, buyeo_outer_url(page), request_timeout, fetcher, meta, "list_requests"
            )
            parsed, _ = _parse_outer_page(soup, page, page, outer_last)
            outer_pages[page] = parsed
        sentinel = _request_soup(
            current,
            buyeo_outer_url(outer_last + 1),
            request_timeout,
            fetcher,
            meta,
            "list_requests",
        )
        sentinel_rows, _ = _parse_outer_page(sentinel, outer_last + 1, outer_last, outer_last)
        if _outer_signature(sentinel_rows) != _outer_signature(outer_pages[outer_last]):
            raise BuyeoContractError("outer immediate post-last clamp differs from final page")
        outer_stability: dict[str, bool] = {}
        for page in dict.fromkeys((1, outer_last)):
            soup = _request_soup(
                current, buyeo_outer_url(page), request_timeout, fetcher, meta, "list_requests"
            )
            parsed, _ = _parse_outer_page(soup, page, page, outer_last)
            stable = _outer_signature(parsed) == _outer_signature(outer_pages[page])
            outer_stability[str(page)] = stable
            if not stable:
                raise BuyeoContractError(f"outer page {page} boundary stability recheck changed")
        meta["outer_stability_rechecks"] = outer_stability
        meta["outer_sentinel_mode"] = "clamped_last"

        for page in range(1, outer_last + 1):
            count = len(outer_pages[page])
            if page < outer_last and count != BUYEO_OUTER_PAGE_SIZE:
                raise BuyeoContractError(f"outer page {page} is not full")
            if page == outer_last and not 1 <= count <= BUYEO_OUTER_PAGE_SIZE:
                raise BuyeoContractError("outer final-page size is invalid")
            programmes.extend(outer_pages[page])
        group_ids = [_clean(row["group_id"]) for row in programmes]
        duplicate_groups = len(group_ids) - len(set(group_ids))
        if duplicate_groups:
            raise BuyeoContractError(f"{duplicate_groups} duplicate programme identities")

        for parent in programmes:
            group_id = _clean(parent["group_id"])
            first = _request_soup(
                current,
                buyeo_group_url(group_id, 1),
                request_timeout,
                fetcher,
                meta,
                "detail_requests",
            )
            first_courses, group_last = _parse_group_page(first, parent, 1, 1)
            meta["detail_pages"] = int(meta["detail_pages"]) + 1
            pages: dict[int, list[dict[str, Any]]] = {1: first_courses}
            for page in range(2, group_last + 1):
                soup = _request_soup(
                    current,
                    buyeo_group_url(group_id, page),
                    request_timeout,
                    fetcher,
                    meta,
                    "detail_requests",
                )
                parsed, _ = _parse_group_page(soup, parent, page, page, group_last)
                meta["detail_pages"] = int(meta["detail_pages"]) + 1
                pages[page] = parsed
            sentinel = _request_soup(
                current,
                buyeo_group_url(group_id, group_last + 1),
                request_timeout,
                fetcher,
                meta,
                "detail_requests",
            )
            sentinel_courses, _ = _parse_group_page(
                sentinel, parent, group_last + 1, group_last, group_last
            )
            if _course_signature(sentinel_courses) != _course_signature(pages[group_last]):
                raise BuyeoContractError(
                    f"programme {group_id} immediate post-last clamp differs from final page"
                )
            for page in dict.fromkeys((1, group_last)):
                soup = _request_soup(
                    current,
                    buyeo_group_url(group_id, page),
                    request_timeout,
                    fetcher,
                    meta,
                    "detail_requests",
                )
                parsed, _ = _parse_group_page(soup, parent, page, page, group_last)
                group_boundary_rechecks += 1
                if _course_signature(parsed) != _course_signature(pages[page]):
                    raise BuyeoContractError(
                        f"programme {group_id} page {page} boundary stability recheck changed"
                    )
            counts = []
            for page in range(1, group_last + 1):
                count = len(pages[page])
                if page < group_last and count != BUYEO_COURSE_PAGE_SIZE:
                    raise BuyeoContractError(
                        f"programme {group_id} page {page} is not full"
                    )
                if page == group_last and not 1 <= count <= BUYEO_COURSE_PAGE_SIZE:
                    raise BuyeoContractError(
                        f"programme {group_id} final-page size is invalid"
                    )
                counts.append(count)
                courses.extend(pages[page])
            group_page_counts[group_id] = counts

        if len(courses) > allowed_details:
            meta["source_cap_reached"] = True
            raise BuyeoContractError(
                f"detail_limit cap allows {allowed_details} of {len(courses)} required course cards"
            )
        identities = [_clean(course["course_id"]) for course in courses]
        duplicate_ids = len(identities) - len(set(identities))
        meta["duplicate_source_id_count"] = duplicate_ids
        if duplicate_ids:
            raise BuyeoContractError(f"{duplicate_ids} duplicate individual-course identities")

        current_courses: list[dict[str, Any]] = []
        expired_count = undated_count = cancelled_count = 0
        for course in courses:
            period = course["period"]
            if period is None:
                undated_count += 1
            elif period[1] < cutoff:
                expired_count += 1
            elif _CANCELLED_RE.search(_clean(course["title"])):
                cancelled_count += 1
            else:
                current_courses.append(course)
        semantic_signatures = [
            (
                BUYEO_OFFICIAL_BRANCH,
                _normalized_text(course["title"]),
                course["period"][0].isoformat(),
                course["period"][1].isoformat(),
                _normalized_text(course["schedule"]),
            )
            for course in current_courses
        ]
        semantic_duplicates = len(semantic_signatures) - len(set(semantic_signatures))
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise BuyeoContractError(f"{semantic_duplicates} duplicate current semantic signatures")

        output = [_output_row(course) for course in current_courses]
        deduped = list((dedupe_rows or _default_dedupe)(output))
        if len(deduped) != len(output):
            raise BuyeoContractError(
                f"dedupe changed complete row count {len(output)} to {len(deduped)}"
            )
        privacy_violations = _privacy_violations(deduped)
        meta["privacy_violations"] = privacy_violations
        if privacy_violations:
            raise BuyeoContractError(f"{privacy_violations} PII allowlist violations")
        deduped.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        meta.update(
            {
                "pages": outer_last,
                "data_pages": outer_last,
                "group_count": len(programmes),
                "group_page_counts": group_page_counts,
                "group_detail_data_pages": int(meta["detail_pages"]),
                "group_sentinel_count": len(programmes),
                "group_boundary_recheck_count": group_boundary_rechecks,
                "required_detail_requests": int(meta["detail_requests"]),
                "source_total": len(courses),
                "source_rows": len(courses),
                "current_count": len(current_courses),
                "returned_count": len(deduped),
                "expired_count": expired_count,
                "undated_historical_count": undated_count,
                "cancelled_count": cancelled_count,
                "detail_attempts": len(courses),
                "detail_verified": len(courses),
                "group_status_counts": dict(Counter(row["source_status"] for row in programmes)),
                "group_selection_method_counts": dict(
                    Counter(row["selection_method"] for row in programmes)
                ),
                "all_source_status_counts": dict(
                    Counter(course["source_status"] for course in courses)
                ),
                "source_status_counts": dict(
                    Counter(course["source_status"] for course in current_courses)
                ),
                "status_counts": dict(Counter(row["status"] for row in deduped)),
                "branch_counts": dict(Counter(row["branch"] for row in deduped)),
                "application_type_counts": dict(
                    Counter(row["application_type"] for row in deduped)
                ),
                "application_control_count": sum(
                    bool(course["application_url"]) for course in current_courses
                ),
                "archived_identity_hint_count": sum(
                    bool(course["archived_identity_hint"]) for course in courses
                ),
                "discarded_instructor_count": sum(bool(course["instructor"]) for course in courses),
                "missing_instructor_historical_count": sum(
                    not bool(course["instructor"]) for course in courses
                ),
                "request_retry_count": int(meta["physical_requests"])
                - int(meta["logical_requests"]),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not deduped,
                "no_current_reason": (
                    "complete Buyeo lifelong ledger contains no current/future courses"
                    if not deduped
                    else ""
                ),
            }
        )
        return deduped, BUYEO_PARSER, meta
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {_clean(exc)}")
        meta.update(
            {
                "pages": outer_last,
                "data_pages": outer_last,
                "group_count": len(programmes),
                "group_page_counts": group_page_counts,
                "group_boundary_recheck_count": group_boundary_rechecks,
                "source_total": len(courses),
                "source_rows": len(courses),
                "detail_attempts": len(courses),
                "request_retry_count": int(meta["physical_requests"])
                - int(meta["logical_requests"]),
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "returned_count": 0,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], BUYEO_PARSER, meta
    finally:
        close = getattr(current, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


collect = collect_buyeo_education


__all__ = [
    "BUYEO_APPLICATION_PATH",
    "BUYEO_CANONICAL_CANDIDATE_ID",
    "BUYEO_CANONICAL_DERIVED_PROVIDER",
    "BUYEO_CANONICAL_URL",
    "BUYEO_DIRECTORY_CANDIDATE_ID",
    "BUYEO_DIRECTORY_URL",
    "BUYEO_HOMEPAGE_CANDIDATE_ID",
    "BUYEO_HOMEPAGE_DERIVED_PROVIDER",
    "BUYEO_HOMEPAGE_URL",
    "BUYEO_HOST",
    "BUYEO_LIST_PATH",
    "BUYEO_MUNICIPALITY_CODE",
    "BUYEO_MUNICIPALITY_NAME",
    "BUYEO_NATIONAL_MUSEUM_URL",
    "BUYEO_OFFICIAL_ADDRESS",
    "BUYEO_OFFICIAL_BRANCH",
    "BUYEO_OWNER_BOUNDARY_AUDIT",
    "BUYEO_OWNERSHIP_SCOPE",
    "BUYEO_PARSER",
    "BUYEO_PII_FIELDS_NEVER_PERSISTED",
    "BUYEO_PROVIDER",
    "BUYEO_SITE_NAME",
    "BuyeoContractError",
    "buyeo_application_url",
    "buyeo_group_url",
    "buyeo_outer_url",
    "buyeo_session_factory",
    "collect",
    "collect_buyeo_education",
    "is_buyeo_education_target",
    "is_target",
]
