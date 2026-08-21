"""Fail-closed collector for Hongseong County lifelong-learning courses.

The registered target used to point at the lifelong-learning home page and a
generic parser returned only a couple of navigation fragments.  The official
course ledger lives below ``/prog/euc/lll/``.  It is also rendered verbatim in
the county-wide reservation site; that second rendering is an alias, not a
second owner.  Agriculture, culture-experience, health, information-education,
resident-centre and room-reservation ledgers are separate owners and are not
followed by this collector.

The ledger is historical and is not ordered strictly by education end date.
Consequently every declared page is read, an immediate empty sentinel is
proved, the first/last/sentinel boundaries are rechecked, and every current or
future record is verified against its public detail.  Application forms and
applicant/private pages are never requested.  Instructor, manager, telephone,
attachment and free-form detail values are deliberately discarded.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


HONGSEONG_PROVIDER = "MUNI_WWW_HONGSEONG_GO_KR_C700BF28"
HONGSEONG_HOME_CANDIDATE_ID = "MUNI_IR_A11602D77C70"
HONGSEONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_817903D7299F"
HONGSEONG_CANONICAL_DERIVED_PROVIDER = "MUNI_WWW_HONGSEONG_GO_KR_EFB52C71"
HONGSEONG_MUNICIPALITY_CODE = "4480000000"
HONGSEONG_MUNICIPALITY_NAME = "충청남도 홍성군"

HONGSEONG_HOST = "www.hongseong.go.kr"
HONGSEONG_HOME_PATH = "/lll.do"
HONGSEONG_LIST_PATH = "/prog/euc/lll/sub06_01/lll/list.do"
HONGSEONG_DETAIL_PATH = "/prog/euc/lll/sub06_01/lll/view.do"
HONGSEONG_APPLICATION_PATH = "/prog/euc_reserve/lll/sub06_01/lll/write.do"
HONGSEONG_CANONICAL_URL = f"https://{HONGSEONG_HOST}{HONGSEONG_LIST_PATH}"
HONGSEONG_HOME_URL = f"https://{HONGSEONG_HOST}{HONGSEONG_HOME_PATH}"
HONGSEONG_INTEGRATED_ALIAS_URL = (
    f"https://{HONGSEONG_HOST}/prog/euc/yeyak/sub01_02/lll/list.do"
)
HONGSEONG_GENERAL_RESERVATION_URL = f"https://{HONGSEONG_HOST}/yeyak/index.do"
HONGSEONG_SPACE_RESERVATION_URL = (
    f"https://{HONGSEONG_HOST}/prog/fcltyResve/lll/sub05_02/lll/list.do"
)
HONGSEONG_AGRICULTURE_URL = (
    f"https://{HONGSEONG_HOST}/prog/euc/yeyak/sub01_01/farm/list.do"
)
HONGSEONG_CULTURE_EXPERIENCE_URL = (
    f"https://{HONGSEONG_HOST}/prog/euc/yeyak/sub01_09/culture/list.do"
)
HONGSEONG_HEALTH_URL = (
    f"https://{HONGSEONG_HOST}/prog/euc/yeyak/sub01_10/health/list.do"
)

HONGSEONG_PAGE_SIZE = 12
HONGSEONG_MAX_PAGES = 200
HONGSEONG_MAX_HTML_BYTES = 3_000_000
HONGSEONG_PARSER = (
    "hongseong_lifelong_declared_historical_ledger+all_pages+"
    "immediate_empty_sentinel+stable_first_last_sentinel+current_details+"
    "identity_bound_list_application_controls+official_business_and_venue+"
    "integrated_alias_deduplication+pii_allowlist"
)

HONGSEONG_OWNER_BOUNDARY_AUDIT: dict[str, dict[str, str]] = {
    "registered_lifelong_home": {
        "url": HONGSEONG_HOME_URL,
        "decision": "retarget_home_shell_to_canonical_course_ledger",
    },
    "canonical_lifelong_ledger": {
        "url": HONGSEONG_CANONICAL_URL,
        "decision": "include_as_the_single_lifelong_education_owner",
    },
    "integrated_reservation_lifelong_alias": {
        "url": HONGSEONG_INTEGRATED_ALIAS_URL,
        "decision": "exclude_exact_replica_of_canonical_lifelong_ledger",
    },
    "agriculture_education": {
        "url": HONGSEONG_AGRICULTURE_URL,
        "decision": "exclude_separate_agriculture_education_owner",
    },
    "culture_experience": {
        "url": HONGSEONG_CULTURE_EXPERIENCE_URL,
        "decision": "exclude_separate_culture_experience_owner",
    },
    "health_programmes": {
        "url": HONGSEONG_HEALTH_URL,
        "decision": "exclude_separate_health_programme_owner",
    },
    "lifelong_space_reservation": {
        "url": HONGSEONG_SPACE_RESERVATION_URL,
        "decision": "exclude_separate_room_and_space_reservation_owner",
    },
    "county_integrated_reservation_home": {
        "url": HONGSEONG_GENERAL_RESERVATION_URL,
        "decision": "exclude_multi_owner_navigation_home",
    },
}

HONGSEONG_OFFICIAL_CENTRE_ADDRESSES = {
    "홍성군평생학습관": "충청남도 홍성군 홍성읍 온천1길 11",
    "신도시평생학습관": "충청남도 홍성군 홍북읍 홍학로 50",
}
# Both the official list and detail publish this historic typo.  It is retained
# in source accounting but is long expired and never rewritten or returned.
HONGSEONG_REVERSED_APPLICATION_PERIOD_ANOMALIES = {
    "383": ("2019-02-12", "2019-02-10"),
}

_BRANCH_CODES = {
    "홍성군평생학습관": "HONGSEONG_LIFELONG_MAIN",
    "신도시평생학습관": "HONGSEONG_LIFELONG_NEW_CITY",
    "읍면평생학습센터": "HONGSEONG_LIFELONG_TOWNSHIP",
    "평생학습카페": "HONGSEONG_LIFELONG_CAFE",
    "50플러스 스쿨": "HONGSEONG_LIFELONG_FIFTY_PLUS",
}
_SEPARATE_OWNER_BRANCHES = frozenset({"홍성군농업기술센터", "홍성군홍주천년문화체험관"})
_ORGAN_OPTIONS = (
    ("", "사업구분"),
    ("OR1", "홍성군평생학습관"),
    ("OR2", "신도시평생학습관"),
    ("OR3", "읍면평생학습센터"),
    ("OR4", "평생학습카페"),
    ("OR5", "50플러스 스쿨"),
    ("OR6", "홍성군농업기술센터"),
    ("OR07", "홍성군홍주천년문화체험관"),
)
_LIST_HEADERS = (
    "사업명",
    "교육과목",
    "교육기간(접수기간)",
    "교육시간",
    "접수자/정원 (대기자)",
    "상태",
)
_DETAIL_REQUIRED = frozenset(
    {
        "사업명",
        "분야",
        "강좌명",
        "교육대상",
        "접수기간",
        "교육기간",
        "교육시간",
        "재료비",
        "강사명",
        "교육장소",
        "담당자",
        "문의전화",
    }
)
_DETAIL_STATES = frozenset(
    {
        "접수중",
        "접수마감",
        "신청마감",
        "접수예정",
        "대기신청",
        "대기중",
        "교육대기",
        "교육예정",
        "교육중",
        "교육종료",
    }
)
_INACTIVE_STATUS = {
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
    "접수예정": "SCHEDULED",
    "대기중": "SCHEDULED",
}
_NO_DATA_TEXT = "접수 예정 또는 접수중인 과목이 없습니다."

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d{0,11}$")
_DATE = re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)")
_CAPACITY = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)(?:\s*\((\d[\d,]*)\))?$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TEST_TITLE = re.compile(r"(?:^|[\s\[\]()-])(?:test|테스트)(?:$|[\s\[\]()-])", re.IGNORECASE)
_CANCELLED_TITLE = re.compile(r"(?:폐강|강좌\s*취소|운영\s*취소)")
_ADDRESS_FRAGMENT = re.compile(
    r"^(?:충청남도\s+홍성군\s+|충남\s+홍성군\s+)?"
    r"(홍성읍|광천읍|홍북읍|금마면|홍동면|장곡면|은하면|결성면|서부면|갈산면|구항면)\s+.+$"
)

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_status",
        "source_detail_state",
        "source_business",
        "source_application_method",
        "source_capacity_current",
        "source_capacity_total",
        "source_waitlist_count",
        "source_education_period",
        "source_application_period",
        "branch_basis",
        "venue_basis",
        "detail_verified",
        "application_control_present",
        "application_endpoint_not_requested",
        "sensitive_detail_fields_discarded",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "manager",
        "instructor",
        "teacher",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
    }
)

SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class HongseongContractError(ValueError):
    """Raised when the audited public Hongseong contract changes."""


@dataclass(frozen=True)
class _Page:
    number: int
    total: int
    last: int
    rows: tuple[dict[str, Any], ...]


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _audit_date(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _strict_url(value: Any, *, path: str, query: list[tuple[str, str]]) -> bool:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
        actual_query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == HONGSEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == path
        and actual_query == query
        and not parsed.fragment
    )


def is_hongseong_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == HONGSEONG_PROVIDER
        and _strict_url(_target_value(target, "url"), path=HONGSEONG_LIST_PATH, query=[])
    )


is_target = is_hongseong_education_target


def hongseong_list_url(page: int = 1) -> str:
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    if page == 1:
        return HONGSEONG_CANONICAL_URL
    return f"{HONGSEONG_CANONICAL_URL}?{urlencode({'pageIndex': page})}"


def hongseong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY.fullmatch(value):
        raise ValueError("invalid Hongseong course identity")
    return f"https://{HONGSEONG_HOST}{HONGSEONG_DETAIL_PATH}?{urlencode({'eduNo': value})}"


def hongseong_session_factory() -> requests.Session:
    session = requests.Session()
    # The official WAF rejects generic bot UAs with HTTP 400.  This stable,
    # browser-shaped header set is required for the public pages; no cookie or
    # private credential is supplied.
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _coerce_soup(value: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise HongseongContractError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise HongseongContractError("redirect history is not accepted")
    headers = getattr(value, "headers", {}) or {}
    if headers.get("Location"):
        raise HongseongContractError("redirect response is not accepted")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type"))
    if content_type and "html" not in content_type.lower():
        raise HongseongContractError("official response is not HTML")
    final_url = _clean(getattr(value, "url", requested_url) or requested_url)
    expected = urlparse(requested_url)
    actual = urlparse(final_url)
    try:
        expected_port = expected.port
        actual_port = actual.port
        expected_query = parse_qsl(expected.query, keep_blank_values=True, strict_parsing=True)
        actual_query = parse_qsl(actual.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError) as exc:
        raise HongseongContractError("malformed official response URL") from exc
    if not (
        actual.scheme == expected.scheme == "https"
        and (actual.hostname or "").lower() == HONGSEONG_HOST
        and (expected.hostname or "").lower() == HONGSEONG_HOST
        and actual_port is expected_port is None
        and actual.username is None
        and actual.password is None
        and actual.path == expected.path
        and actual_query == expected_query
        and not actual.fragment
    ):
        raise HongseongContractError("official response URL changed")
    content = getattr(value, "content", None)
    if content is None:
        content = str(getattr(value, "text", value)).encode("utf-8")
    if not content:
        raise HongseongContractError("empty official response")
    if len(content) > HONGSEONG_MAX_HTML_BYTES:
        raise HongseongContractError("HTML size cap exceeded")
    return BeautifulSoup(content, "html.parser")


def _dates(value: Any, *, identity: str, field: str) -> tuple[date, date]:
    matches = _DATE.findall(_clean(value))
    if len(matches) != 2:
        raise HongseongContractError(f"course {identity}: {field} must contain two dates")
    start, end = (date(int(year), int(month), int(day)) for year, month, day in matches)
    if end < start:
        raise HongseongContractError(f"course {identity}: reversed {field}")
    return start, end


def _total_and_form(soup: BeautifulSoup, page: int) -> int:
    root = soup.select_one("#txt")
    if root is None:
        raise HongseongContractError(f"page {page}: content root missing")
    forms = root.select(f'form[name="listForm"][action="{HONGSEONG_LIST_PATH}"]')
    if len(forms) != 1 or _clean(forms[0].get("method")).upper() != "POST":
        raise HongseongContractError(f"page {page}: canonical list form changed")
    form = forms[0]
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select('input[type="hidden"][name]')
    }
    if hidden != {"siteCode": "lll", "mno": "sub06_01", "pageIndex": "1"}:
        raise HongseongContractError(f"page {page}: list form scope changed")
    organ = form.select_one('select[name="organ"]')
    options = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in (organ.select("option") if organ else ())
    )
    if options != _ORGAN_OPTIONS:
        raise HongseongContractError(f"page {page}: official business partitions changed")
    condition = form.select_one('select[name="searchCondition"]')
    condition_options = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in (condition.select("option") if condition else ())
    )
    if condition_options != (("subject", "교육과목"),):
        raise HongseongContractError(f"page {page}: search contract changed")
    total_node = form.select_one(".program--count strong")
    total_text = _clean(total_node.get_text(" ", strip=True) if total_node else "")
    if not re.fullmatch(r"\d[\d,]*", total_text):
        raise HongseongContractError(f"page {page}: declared total missing")
    total = int(total_text.replace(",", ""))
    if total < 1:
        raise HongseongContractError("canonical lifelong ledger unexpectedly has no source rows")
    return total


def _application_control(cell: Any, *, page: int, identity: str) -> tuple[str, str, str]:
    anchors = cell.select("a[href]")
    plain = _clean(cell.get_text(" ", strip=True))
    if not anchors:
        status = _INACTIVE_STATUS.get(plain)
        if status is None:
            raise HongseongContractError(f"course {identity}: unknown inactive status {plain!r}")
        return status, "", plain
    if len(anchors) != 1:
        raise HongseongContractError(f"course {identity}: application control count changed")
    anchor = anchors[0]
    text = _clean(anchor.get_text(" ", strip=True))
    url = urljoin(HONGSEONG_CANONICAL_URL, _clean(anchor.get("href")))
    parsed = urlparse(url)
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise HongseongContractError(f"course {identity}: malformed application URL") from exc
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == HONGSEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == HONGSEONG_APPLICATION_PATH
        and set(query) == {"pageIndex", "eduNo", "resvChk"}
        and query["pageIndex"] == [str(page)]
        and query["eduNo"] == [identity]
        and query["resvChk"] in (["N"], ["Y"])
        and not parsed.fragment
    ):
        raise HongseongContractError(f"course {identity}: application identity/path drift")
    if text not in {"접수하기", "대기신청", "대기접수"}:
        raise HongseongContractError(f"course {identity}: application label changed")
    status = "WAITING" if query["resvChk"] == ["Y"] else "OPEN"
    return status, url, text


def _parse_list_row(row: Any, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 6:
        raise HongseongContractError(f"page {page}: list row cell count changed")
    business = _clean(cells[0].get_text(" ", strip=True))
    if business in _SEPARATE_OWNER_BRANCHES:
        raise HongseongContractError(f"page {page}: separate owner leaked into lifelong ledger")
    if business not in _BRANCH_CODES:
        raise HongseongContractError(f"page {page}: unknown lifelong business {business!r}")
    anchors = cells[1].select('a[href*="eduNo="]')
    if len(anchors) != 1:
        raise HongseongContractError(f"page {page}: course detail control changed")
    anchor = anchors[0]
    title = _clean(anchor.get_text(" ", strip=True))
    detail_url = urljoin(HONGSEONG_CANONICAL_URL, _clean(anchor.get("href")))
    parsed = urlparse(detail_url)
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise HongseongContractError(f"page {page}: malformed detail URL") from exc
    identity = _clean((query.get("eduNo") or [""])[0])
    if not (
        title
        and _IDENTITY.fullmatch(identity)
        and parsed.scheme == "https"
        and (parsed.hostname or "").lower() == HONGSEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == HONGSEONG_DETAIL_PATH
        and set(query) == {"eduNo"}
        and len(query["eduNo"]) == 1
        and not parsed.fragment
    ):
        raise HongseongContractError(f"page {page}: detail identity/path drift")
    periods = _DATE.findall(_clean(cells[2].get_text(" ", strip=True)))
    if len(periods) != 4:
        raise HongseongContractError(f"course {identity}: list periods changed")
    event_start, event_end = (
        date(int(year), int(month), int(day)) for year, month, day in periods[:2]
    )
    apply_start, apply_end = (
        date(int(year), int(month), int(day)) for year, month, day in periods[2:]
    )
    period_anomaly = apply_end < apply_start
    if event_end < event_start or (
        period_anomaly
        and HONGSEONG_REVERSED_APPLICATION_PERIOD_ANOMALIES.get(identity)
        != (apply_start.isoformat(), apply_end.isoformat())
    ):
        raise HongseongContractError(f"course {identity}: reversed list period")
    schedule = _clean(cells[3].get_text(" ", strip=True))
    # A small number of historical rows (for example eduNo=569) officially
    # publish a blank schedule.  Preserve that source fact; current rows are
    # still identity-checked against their detail instead of inventing a time.
    capacity_node = cells[4].select_one("strong")
    method_node = cells[4].select_one("p")
    capacity_text = _clean(capacity_node.get_text(" ", strip=True) if capacity_node else "")
    method = _clean(method_node.get_text(" ", strip=True) if method_node else "")
    capacity = _CAPACITY.fullmatch(capacity_text)
    if capacity is None or method not in {"선착순", "추첨"}:
        raise HongseongContractError(f"course {identity}: capacity/application method changed")
    status, application_url, source_status = _application_control(
        cells[5], page=page, identity=identity
    )
    return {
        "identity": identity,
        "page": page,
        "title": title,
        "business": business,
        "detail_url": detail_url,
        "event_start": event_start,
        "event_end": event_end,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "period_anomaly": period_anomaly,
        "schedule": schedule,
        "capacity_current": int(capacity.group(1).replace(",", "")),
        "capacity_total": int(capacity.group(2).replace(",", "")),
        "waitlist_count": int((capacity.group(3) or "0").replace(",", "")),
        "method": method,
        "status": status,
        "source_status": source_status,
        "application_url": application_url,
    }


def _parse_list_page(soup: BeautifulSoup, page: int) -> _Page:
    total = _total_and_form(soup, page)
    last = math.ceil(total / HONGSEONG_PAGE_SIZE)
    tables = [
        table
        for table in soup.select("#txt table.table")
        if _clean((table.select_one("caption strong") or {}).get_text(" ", strip=True)
                  if table.select_one("caption strong") else "")
        == "교육신청 및 확인 목록"
    ]
    if len(tables) != 1:
        raise HongseongContractError(f"page {page}: canonical course table changed")
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select("thead th"))
    if headers != _LIST_HEADERS:
        raise HongseongContractError(f"page {page}: list headers changed")
    body_rows = table.select("tbody > tr")
    rows: list[dict[str, Any]] = []
    no_data_rows = 0
    for row in body_rows:
        if row.select_one('a[href*="eduNo="]') is None:
            text = _clean(row.get_text(" ", strip=True))
            if text != _NO_DATA_TEXT or len(body_rows) != 1:
                raise HongseongContractError(f"page {page}: unrecognized structural row")
            no_data_rows += 1
            continue
        rows.append(_parse_list_row(row, page))
    expected = (
        min(HONGSEONG_PAGE_SIZE, total - ((page - 1) * HONGSEONG_PAGE_SIZE))
        if page <= last
        else 0
    )
    if len(rows) != expected:
        raise HongseongContractError(
            f"page {page}: expected {expected} source rows, found {len(rows)}"
        )
    if page <= last and no_data_rows:
        raise HongseongContractError(f"page {page}: premature no-data row")
    if page > last and no_data_rows != 1:
        raise HongseongContractError(f"page {page}: empty sentinel structure changed")
    identities = [str(row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise HongseongContractError(f"page {page}: duplicate identities")
    return _Page(page, total, last, tuple(rows))


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        tuple(
            (
                row["identity"],
                row["title"],
                row["business"],
                row["event_start"],
                row["event_end"],
                row["source_status"],
                row["application_url"],
            )
            for row in page.rows
        ),
    )


def _detail_fields(table: Any, identity: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in table.select("tbody > tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index + 1 < len(cells):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise HongseongContractError(f"course {identity}: detail cell pairing changed")
            label = _clean(cells[index].get_text(" ", strip=True))
            value = _clean(cells[index + 1].get_text(" ", strip=True))
            if not label or (label in fields and fields[label] != value):
                raise HongseongContractError(f"course {identity}: conflicting detail field")
            fields[label] = value
            index += 2
        if index != len(cells):
            raise HongseongContractError(f"course {identity}: unpaired detail cell")
    if not _DETAIL_REQUIRED <= set(fields):
        missing = sorted(_DETAIL_REQUIRED - set(fields))
        raise HongseongContractError(f"course {identity}: detail fields missing {missing}")
    if not set(fields) <= (_DETAIL_REQUIRED | {"첨부파일"}):
        raise HongseongContractError(f"course {identity}: unaudited detail fields appeared")
    return fields


def _venue_address(business: str, venue: str, identity: str) -> tuple[str, str]:
    if not venue:
        raise HongseongContractError(f"course {identity}: education venue missing")
    parentheticals = re.findall(r"\(([^()]*)\)", venue)
    fragment = ""
    for candidate in reversed(parentheticals):
        candidate = _clean(candidate)
        if _ADDRESS_FRAGMENT.fullmatch(candidate):
            fragment = candidate
            break
    venue_name = _clean(re.sub(r"\s*\([^()]*\)\s*$", "", venue))
    if not venue_name:
        venue_name = venue
    if fragment:
        fragment = re.sub(r"^(?:충청남도|충남)\s+홍성군\s+", "", fragment)
        address = f"충청남도 홍성군 {fragment}"
    elif venue_name.startswith("홍성군평생학습관"):
        address = HONGSEONG_OFFICIAL_CENTRE_ADDRESSES["홍성군평생학습관"]
    elif venue_name.startswith("신도시평생학습관"):
        address = HONGSEONG_OFFICIAL_CENTRE_ADDRESSES["신도시평생학습관"]
    elif business in HONGSEONG_OFFICIAL_CENTRE_ADDRESSES:
        address = HONGSEONG_OFFICIAL_CENTRE_ADDRESSES[business]
    else:
        raise HongseongContractError(f"course {identity}: official venue address missing")
    return venue_name, address


def _row_from_detail(listed: Mapping[str, Any], soup: BeautifulSoup) -> tuple[dict[str, Any], int, int]:
    identity = str(listed["identity"])
    roots = soup.select("#txt .lecture_info")
    if len(roots) != 1:
        raise HongseongContractError(f"course {identity}: detail root changed")
    root = roots[0]
    state_node = root.select_one(":scope > em")
    heading_node = root.select_one(":scope > h2")
    detail_state = _clean(state_node.get_text(" ", strip=True) if state_node else "")
    heading = _clean(heading_node.get_text(" ", strip=True) if heading_node else "")
    if detail_state not in _DETAIL_STATES or heading != listed["title"]:
        raise HongseongContractError(f"course {identity}: detail state/title drift")
    tables = [
        table
        for table in root.select("table.table")
        if _clean((table.select_one("caption strong") or {}).get_text(" ", strip=True)
                  if table.select_one("caption strong") else "")
        == "강좌 정보표"
    ]
    if len(tables) != 1:
        raise HongseongContractError(f"course {identity}: detail table changed")
    fields = _detail_fields(tables[0], identity)
    detail_start, detail_end = _dates(fields["교육기간"], identity=identity, field="education period")
    apply_start, apply_end = _dates(fields["접수기간"], identity=identity, field="application period")
    if not (
        fields["사업명"] == listed["business"]
        and fields["강좌명"] == listed["title"]
        and (detail_start, detail_end) == (listed["event_start"], listed["event_end"])
        and (apply_start, apply_end) == (listed["apply_start"], listed["apply_end"])
        and fields["교육시간"] == listed["schedule"]
    ):
        raise HongseongContractError(f"course {identity}: list/detail identity drift")
    venue = fields["교육장소"]
    venue_name, address = _venue_address(str(listed["business"]), venue, identity)
    application_url = str(listed["application_url"])
    status = str(listed["status"])
    application_type = (
        "ONLINE_WAITLIST_LOGIN_REQUIRED"
        if status == "WAITING"
        else "ONLINE_RESERVATION_LOGIN_REQUIRED"
        if status == "OPEN"
        else "INFO_ONLY"
    )
    period = f"{detail_start.isoformat()} ~ {detail_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    sensitive_discarded = sum(
        bool(_clean(fields.get(label))) for label in ("강사명", "담당자", "문의전화")
    )
    attachment_discarded = int(bool(_clean(fields.get("첨부파일"))))
    row: dict[str, Any] = {
        "provider": HONGSEONG_PROVIDER,
        "provider_course_id": f"{HONGSEONG_PROVIDER}:edu:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": str(listed["business"]),
        "branch_code": _BRANCH_CODES[str(listed["business"])],
        "preserve_branch": True,
        "category": fields["분야"],
        "program_type": "교육",
        "raw_url": hongseong_detail_url(identity),
        "application_url": application_url,
        "application_type": application_type,
        "application_method": str(listed["method"]),
        "reservation_available": bool(application_url),
        "status": status,
        "fee": fields["재료비"],
        "period": period,
        "start_date": detail_start.isoformat(),
        "end_date": detail_end.isoformat(),
        "apply_period": apply_period,
        "schedule_raw": str(listed["schedule"]),
        "capacity": str(listed["capacity_total"]),
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": int(listed["capacity_total"]),
        "waitlist_count": int(listed["waitlist_count"]),
        "target": fields["교육대상"],
        "venue": venue,
        "venue_name": venue_name,
        "address": address,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": HONGSEONG_PARSER,
        "municipality_code": HONGSEONG_MUNICIPALITY_CODE,
        "municipality_name": HONGSEONG_MUNICIPALITY_NAME,
        "municipality_full_name": HONGSEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_status": str(listed["source_status"]),
            "source_detail_state": detail_state,
            "source_business": str(listed["business"]),
            "source_application_method": str(listed["method"]),
            "source_capacity_current": int(listed["capacity_current"]),
            "source_capacity_total": int(listed["capacity_total"]),
            "source_waitlist_count": int(listed["waitlist_count"]),
            "source_education_period": period,
            "source_application_period": apply_period,
            "branch_basis": "official list 사업명",
            "venue_basis": "identity-verified detail 교육장소",
            "detail_verified": True,
            "application_control_present": bool(application_url),
            "application_endpoint_not_requested": True,
            "sensitive_detail_fields_discarded": sensitive_discarded,
            "service_family": "education",
        },
    }
    return row, sensitive_discarded, attachment_discarded


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden detail/PII key")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw field allowlist exceeded")
    payload = repr(row)
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("contact data persisted")
    return errors


def _semantic_signature(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).casefold()),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("schedule_raw")),
        _clean(row.get("branch")),
        _clean(row.get("venue_name")),
    )


def collect_hongseong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = HONGSEONG_MAX_PAGES,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[HtmlFetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future Hongseong lifelong snapshot."""

    cutoff = _audit_date(today)
    factory = session_factory or hongseong_session_factory
    html_fetcher = fetcher or _default_fetcher
    meta: dict[str, Any] = {
        "municipality_code": HONGSEONG_MUNICIPALITY_CODE,
        "municipality_name": HONGSEONG_MUNICIPALITY_NAME,
        "owner_provider": HONGSEONG_PROVIDER,
        "candidate_id": HONGSEONG_HOME_CANDIDATE_ID,
        "canonical_candidate_id": HONGSEONG_CANONICAL_CANDIDATE_ID,
        "canonical_url": HONGSEONG_CANONICAL_URL,
        "parser": HONGSEONG_PARSER,
        "cutoff": cutoff.isoformat(),
        "pages": 0,
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "session_refresh_count": 0,
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
    session_request_count = 0
    last_request_started = 0.0
    live_throttle = fetcher is None

    def load(url: str, *, kind: str) -> BeautifulSoup:
        nonlocal session, session_request_count, last_request_started
        meta["list_requests" if kind == "list" else "detail_pages"] += 1
        # The public WAF starts rejecting a long-lived HTTP connection around
        # its 101st request even though the same public URL succeeds in a fresh
        # browser session.  Rotate well before that deterministic boundary;
        # this does not carry authentication or private cookies.
        if session_request_count >= 75:
            close = getattr(session, "close", None)
            if callable(close):
                close()
            session = factory()
            session_request_count = 0
            meta["session_refresh_count"] += 1
        last_error: Optional[BaseException] = None
        for attempt in range(3):
            if live_throttle:
                remaining = 0.42 - (time.monotonic() - last_request_started)
                if remaining > 0:
                    time.sleep(remaining)
                last_request_started = time.monotonic()
            meta["physical_requests"] += 1
            session_request_count += 1
            try:
                value = html_fetcher(session, url, timeout)
                status = int(getattr(value, "status_code", 200))
                blocked_body = bytes(getattr(value, "content", b"") or b"")
                waf_blocked = status == 400 and b"Request Blocked" in blocked_body
                if (status in {429, 500, 502, 503, 504} or waf_blocked) and attempt < 2:
                    meta["request_retry_count"] += 1
                    if waf_blocked:
                        close = getattr(session, "close", None)
                        if callable(close):
                            close()
                        session = factory()
                        session_request_count = 0
                        meta["session_refresh_count"] += 1
                        time.sleep(5.0 * (attempt + 1))
                    else:
                        time.sleep(0.3 * (attempt + 1))
                    continue
                return _coerce_soup(value, url)
            except (requests.RequestException, TimeoutError) as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                meta["request_retry_count"] += 1
                time.sleep(0.3 * (attempt + 1))
        assert last_error is not None
        raise last_error

    try:
        if not is_hongseong_education_target(target):
            raise HongseongContractError("target is not the canonical Hongseong lifelong owner")
        if timeout < 1 or max_pages < 1 or detail_limit < 0:
            raise HongseongContractError("invalid collector limits")
        session = factory()
        first = _parse_list_page(load(hongseong_list_url(1), kind="list"), 1)
        required_pages = first.last + 1
        if max_pages < required_pages:
            meta["source_cap_reached"] = True
            raise HongseongContractError(
                f"max_pages {max_pages} below required {required_pages} including sentinel"
            )
        pages: dict[int, _Page] = {1: first}
        for page_number in range(2, first.last + 1):
            parsed = _parse_list_page(load(hongseong_list_url(page_number), kind="list"), page_number)
            if parsed.total != first.total or parsed.last != first.last:
                raise HongseongContractError(f"page {page_number}: declared boundary changed")
            pages[page_number] = parsed
        sentinel_number = first.last + 1
        sentinel = _parse_list_page(
            load(hongseong_list_url(sentinel_number), kind="list"), sentinel_number
        )
        if sentinel.rows or sentinel.total != first.total or sentinel.last != first.last:
            raise HongseongContractError("immediate post-last sentinel changed")

        boundary_rechecks = 0
        for page_number, expected in (
            (1, pages[1]),
            (first.last, pages[first.last]),
            (sentinel_number, sentinel),
        ):
            check = _parse_list_page(
                load(hongseong_list_url(page_number), kind="list"), page_number
            )
            boundary_rechecks += 1
            if _page_signature(check) != _page_signature(expected):
                raise HongseongContractError(f"page {page_number}: stability recheck changed")

        listed = [row for page in range(1, first.last + 1) for row in pages[page].rows]
        if len(listed) != first.total:
            raise HongseongContractError("declared total and all-page source rows differ")
        identities = [str(row["identity"]) for row in listed]
        if len(identities) != len(set(identities)):
            raise HongseongContractError("duplicate identities across declared pages")
        current_source = [row for row in listed if row["event_end"] >= cutoff]
        expired_count = len(listed) - len(current_source)
        cancelled = [row for row in current_source if _CANCELLED_TITLE.search(str(row["title"]))]
        test_rows = [row for row in current_source if _TEST_TITLE.search(str(row["title"]))]
        current = [row for row in current_source if row not in cancelled and row not in test_rows]
        if len(current) > detail_limit:
            meta["source_cap_reached"] = True
            raise HongseongContractError("detail_limit would create a partial current snapshot")

        output: list[dict[str, Any]] = []
        sensitive_discarded = 0
        attachments_discarded = 0
        for listed_row in current:
            detail = load(str(listed_row["detail_url"]), kind="detail")
            row, sensitive_count, attachment_count = _row_from_detail(listed_row, detail)
            privacy = _privacy_errors(row)
            if privacy:
                raise HongseongContractError(
                    f"course {listed_row['identity']}: {'; '.join(privacy)}"
                )
            output.append(row)
            sensitive_discarded += sensitive_count
            attachments_discarded += attachment_count
        signatures = [_semantic_signature(row) for row in output]
        semantic_duplicate_count = len(signatures) - len(set(signatures))
        if semantic_duplicate_count:
            raise HongseongContractError("current snapshot contains semantic duplicate courses")
        before_dedupe = len(output)
        if dedupe_rows is not None:
            output = list(dedupe_rows(output))
        if len(output) != before_dedupe:
            raise HongseongContractError("external dedupe removed identity-verified official rows")

        meta.update(
            {
                "pages": first.last,
                "data_pages": first.last,
                "declared_total": first.total,
                "source_rows": len(listed),
                "source_total": len(listed),
                "page_counts": {page: len(value.rows) for page, value in pages.items()},
                "empty_sentinel_page": sentinel_number,
                "boundary_rechecks": boundary_rechecks,
                "source_status_counts": dict(Counter(str(row["source_status"]) for row in listed)),
                "current_source_count": len(current_source),
                "current_education_count": len(current),
                "expired_count": expired_count,
                "period_anomaly_count": sum(bool(row["period_anomaly"]) for row in listed),
                "excluded_cancelled_count": len(cancelled),
                "excluded_test_record_count": len(test_rows),
                "detail_attempts": len(current),
                "detail_verified": len(output),
                "detail_state_counts": dict(
                    Counter(str(row["raw_fields"]["source_detail_state"]) for row in output)
                ),
                "status_counts": dict(Counter(str(row["status"]) for row in output)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in output)),
                "venue_counts": dict(Counter(str(row["venue_name"]) for row in output)),
                "address_count": sum(bool(row.get("address")) for row in output),
                "application_control_count": sum(bool(row.get("application_url")) for row in output),
                "online_application_count": sum(row.get("status") == "OPEN" for row in output),
                "waitlist_application_count": sum(row.get("status") == "WAITING" for row in output),
                "info_only_count": sum(not row.get("application_url") for row in output),
                "sensitive_detail_fields_discarded": sensitive_discarded,
                "attachment_fields_discarded": attachments_discarded,
                "freeform_detail_blocks_persisted": 0,
                "pii_values_persisted": 0,
                "semantic_duplicate_count": semantic_duplicate_count,
                "returned_count": len(output),
                "output_rows": len(output),
                "logical_requests": int(meta["list_requests"]) + int(meta["detail_pages"]),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "no_current_data": not output,
                "no_current_reason": (
                    f"{cutoff.isoformat()} 기준 공식 원장에 현재·향후 평생학습 강좌가 없음"
                    if not output
                    else ""
                ),
            }
        )
        return output, HONGSEONG_PARSER, meta
    except Exception as exc:  # Every network/schema/privacy anomaly fails closed.
        meta.update(
            {
                "configured_collection_error": f"{type(exc).__name__}: {_clean(exc)}",
                "returned_count": 0,
                "output_rows": 0,
                "logical_requests": int(meta.get("list_requests") or 0)
                + int(meta.get("detail_pages") or 0),
                "pagination_complete": False,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
            }
        )
        return [], HONGSEONG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_hongseong_education


__all__ = [
    "HONGSEONG_PROVIDER",
    "HONGSEONG_HOME_CANDIDATE_ID",
    "HONGSEONG_CANONICAL_CANDIDATE_ID",
    "HONGSEONG_CANONICAL_DERIVED_PROVIDER",
    "HONGSEONG_MUNICIPALITY_CODE",
    "HONGSEONG_MUNICIPALITY_NAME",
    "HONGSEONG_CANONICAL_URL",
    "HONGSEONG_INTEGRATED_ALIAS_URL",
    "HONGSEONG_OWNER_BOUNDARY_AUDIT",
    "HONGSEONG_OFFICIAL_CENTRE_ADDRESSES",
    "HONGSEONG_REVERSED_APPLICATION_PERIOD_ANOMALIES",
    "HONGSEONG_PARSER",
    "HongseongContractError",
    "hongseong_list_url",
    "hongseong_detail_url",
    "is_hongseong_education_target",
    "is_target",
    "collect_hongseong_education",
    "collect",
]
