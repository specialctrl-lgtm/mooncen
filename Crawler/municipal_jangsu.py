"""Fail-closed collector for Jangsu County's complete education ledger.

The incumbent public-reservation target already points at the correct owner:
the integrated reservation education list at menu 503001.  The review
candidate is only one expired detail (GJRE0000527), so it is retargeted to the
incumbent list without changing provider ownership.

The source advertises a total and a last page.  Requests beyond that boundary
are clamped to the last page instead of returning an empty table.  A publishable
snapshot therefore proves all advertised pages, the exact clamped overflow
boundary, stable first/last/overflow edges, unique GJRE identities, and every
current/future detail.  Application, reservation lookup, attachment, and other
PII-bearing endpoints are validated where applicable but never fetched.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JANGSU_PROVIDER = "MUNI_WWW_JANGSU_GO_KR_2100CCEA"
JANGSU_CANONICAL_CANDIDATE_ID = "MUNI_IR_B91DBABB7514"
JANGSU_REVIEW_DETAIL_CANDIDATE_ID = "MUNI_IR_CC393DF47961"
JANGSU_GENERAL_PAGE_CANDIDATE_ID = "MUNI_IR_B95181F7BCA2"
JANGSU_PARENT_ALIAS_CANDIDATE_ID = "MUNI_IR_2E4674B4F892"
JANGSU_MUNICIPALITY_CODE = "5274000000"
JANGSU_MUNICIPALITY_NAME = "전북특별자치도 장수군"

JANGSU_HOST = "www.jangsu.go.kr"
JANGSU_PATH = "/reserve/index.jangsu"
JANGSU_LIST_MENU = "DOM_000000503001000000"
JANGSU_DETAIL_MENU = "DOM_000000503002000000"
JANGSU_APPLICATION_MENU = "DOM_000000503003000000"
JANGSU_PARENT_MENU = "DOM_000000503000000000"
JANGSU_CANONICAL_URL = (
    f"https://{JANGSU_HOST}{JANGSU_PATH}?menuCd={JANGSU_LIST_MENU}"
)
JANGSU_REVIEW_DETAIL_URL = (
    f"https://{JANGSU_HOST}{JANGSU_PATH}?"
    f"menuCd={JANGSU_DETAIL_MENU}&reUniqId=GJRE0000527"
)
JANGSU_PAGE_SIZE = 10
JANGSU_RECOMMENDED_MAX_PAGES = 70
JANGSU_RECOMMENDED_DETAIL_LIMIT = 50
JANGSU_RECOMMENDED_MAX_WORKERS = 3
JANGSU_FETCH_ATTEMPTS = 2
JANGSU_MAX_HTML_BYTES = 2_000_000
JANGSU_PARSER = (
    "jangsu_complete_education_gjre_ledger+advertised_total_and_pages+"
    "clamped_overflow_boundary+stable_first_last_overflow+all_current_details+"
    "identity_bound_application_controls_no_fetch+pii_and_free_text_allowlist"
)
JANGSU_OWNERSHIP_SCOPE = (
    "jangsu_integrated_reservation_complete_education_gjre_identity_ledger"
)


class JangsuContractError(ValueError):
    """Raised when the official source no longer satisfies the audited contract."""


@dataclass(frozen=True)
class JangsuBranch:
    code: str
    name: str


JANGSU_BRANCHES: tuple[JangsuBranch, ...] = (
    JangsuBranch("JANGSU_WOMEN_CULTURE_CENTER", "여성문화센터"),
    JangsuBranch("JANGSU_COUNTY", "장수군"),
    JangsuBranch("JANGSU_YOUTH_CULTURE_HOUSE", "청소년문화의집"),
    JangsuBranch("JANGSU_LIBRARY_LEGACY", "도서관"),
    JangsuBranch("JANGSU_AGRICULTURAL_TECH_CENTER", "농업기술센터"),
    JangsuBranch("JANGSU_RURAL_SUPPORT", "농촌지원"),
)
JANGSU_BRANCH_BY_NAME = {item.name: item for item in JANGSU_BRANCHES}

JANGSU_CATEGORIES: tuple[str, ...] = (
    "문화예술",
    "평생교육",
    "청소년문화",
    "평생학습",
    "독서문화",
    "정보화교육",
    "전문화교육",
)
JANGSU_CATEGORY_FILTERS: tuple[tuple[str, str], ...] = (
    ("AA", "문화예술"),
    ("BB", "평생교육"),
    ("CC", "정보화교육"),
    ("DD", "청소년문화"),
    ("EE", "독서문화"),
    ("FF", "평생학습"),
    ("GG", "전문화교육"),
)
JANGSU_LIST_HEADERS = ("번호", "상태", "분야", "강좌명", "기간", "예비인원")
JANGSU_DETAIL_LABELS = (
    "분야",
    "상태",
    "수강료",
    "교재비용",
    "수강인원",
    "교육시설",
    "교육기간",
    "강의시간",
    "접수기간",
    "대상",
    "접수방법",
    "교육장소",
    "담당자",
    "문의처",
    "강사명",
    "강사소개",
)
JANGSU_DISCARDED_DETAIL_FIELDS = (
    "담당자",
    "문의처",
    "강사명",
    "강사소개",
    "강좌 상세정보",
    "첨부파일",
    "이미지",
)
JANGSU_STATUS_MAP: Mapping[str, str] = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "강좌중": "CLOSED",
    "강좌마감": "CLOSED",
}
JANGSU_STATUS_CLASSES: Mapping[str, frozenset[str]] = {
    "접수예정": frozenset({"btn_st", "btn_stbg02"}),
    "접수중": frozenset({"btn_st", "btn_stbg01"}),
    "접수마감": frozenset({"btn_st", "btn_stbg02"}),
    "강좌중": frozenset({"btn_st", "btn_stbg03"}),
    "강좌마감": frozenset({"btn_st", "btn_stbg03"}),
}

JANGSU_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    JANGSU_CANONICAL_CANDIDATE_ID: {
        "provider": JANGSU_PROVIDER,
        "url": JANGSU_CANONICAL_URL,
        "decision": "keep_incumbent_provider_and_complete_education_owner",
    },
    JANGSU_REVIEW_DETAIL_CANDIDATE_ID: {
        "provider": JANGSU_PROVIDER,
        "url": JANGSU_REVIEW_DETAIL_URL,
        "decision": "retarget_expired_single_gjre_detail_to_incumbent_complete_list",
    },
    JANGSU_GENERAL_PAGE_CANDIDATE_ID: {
        "provider": "MUNI_WWW_JANGSU_GO_KR_66C83E96",
        "url": "https://www.jangsu.go.kr/index.jangsu?contentsSid=454",
        "decision": "exclude_general_content_shell_without_course_identities",
    },
    JANGSU_PARENT_ALIAS_CANDIDATE_ID: {
        "provider": JANGSU_PROVIDER,
        "url": (
            f"https://{JANGSU_HOST}{JANGSU_PATH}?menuCd={JANGSU_PARENT_MENU}"
        ),
        "decision": "exclude_parent_menu_duplicate_rendering_of_canonical_list",
    },
}

JANGSU_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://lib.jangsu.go.kr/longvt/uce/bbs/openProgramList.do?mi=MN0029",
        "decision": "exclude_separate_active_municipal_library_IDX_program_owner",
        "owner": "JANGSU_MUNICIPAL_LIBRARY_SEPARATE_OWNER",
    },
    {
        "url": "https://lib.jbe.go.kr/jpl/index.do",
        "decision": "exclude_separate_jeonbuk_education_office_library_owner",
        "owner": "JBE_JANGSU_LIBRARY_SEPARATE_OWNER",
    },
    {
        "url": (
            f"https://{JANGSU_HOST}{JANGSU_PATH}?"
            "menuCd=DOM_000000502001000000"
        ),
        "decision": "exclude_separate_experience_calendar_owner",
        "owner": "",
    },
    {
        "url": (
            f"https://{JANGSU_HOST}{JANGSU_PATH}?"
            "menuCd=DOM_000000502004000000"
        ),
        "decision": "exclude_separate_water_play_reservation_owner",
        "owner": "",
    },
    {
        "url": (
            f"https://{JANGSU_HOST}{JANGSU_PATH}?"
            "menuCd=DOM_000000501000000000"
        ),
        "decision": "exclude_separate_performance_and_culture_event_board",
        "owner": "",
    },
    {
        "url": (
            f"https://{JANGSU_HOST}{JANGSU_PATH}?"
            "menuCd=DOM_000000504000000000"
        ),
        "decision": "exclude_pii_bearing_reservation_lookup",
        "owner": "",
    },
)

JANGSU_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "advertised_total": 613,
    "data_pages": 62,
    "page_counts": [10] * 61 + [3],
    "overflow_page": 63,
    "source_rows": 613,
    "source_status_counts": {
        "강좌마감": 598,
        "강좌중": 9,
        "접수중": 4,
        "접수마감": 2,
    },
    "current_rows": 15,
    "current_status_counts": {"CLOSED": 11, "OPEN": 4},
    "detail_pages": 15,
    "application_controls": 4,
    "expected_requests": 81,
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^GJRE\d{7}$")
_TITLE_BRANCH = re.compile(r"^\[([^\[\]]+)\]\s*(\S.+|\S)$")
_LIST_PERIOD = re.compile(
    r"^신청\s*:\s*(20\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s+교육\s*:\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_DATE_RANGE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_CAPACITY = re.compile(r"^(\d[\d,]*)명\s*/\s*(\d[\d,]*)명$")
_PAGE_CALL = re.compile(r"(?:javascript:)?linkPage\((\d+)\)")
_PHONE = re.compile(
    r"(?<!\d)(?:0\d{1,2}[\s().-]*)?\d{3,4}[\s.-]+\d{4}(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_ID = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_EXCLUDED_TITLE = re.compile(r"(?:테스트|시험용|신청\s*금지|점검|취소|폐강)", re.IGNORECASE)

_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_sequence",
        "source_status",
        "source_category",
        "source_branch",
        "source_apply_period",
        "source_education_period",
        "source_apply_start_time",
        "source_waitlist_total",
        "source_schedule",
        "source_target",
        "source_facility",
        "source_venue",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_endpoint_fetched",
        "reservation_lookup_endpoint_fetched",
        "attachment_endpoint_fetched",
        "discarded_detail_fields",
        "address_policy",
        "service_family",
    }
)
_FORBIDDEN_ROW_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "contacts",
        "manager",
        "manager_name",
        "instructor",
        "instructor_name",
        "instructor_intro",
        "attachments",
        "attachment_urls",
        "course_content",
        "detail_description",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
        "password",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def is_jangsu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JANGSU_PROVIDER:
        return False
    url = _clean(_target_value(target, "url"))
    if url != JANGSU_CANONICAL_URL:
        return False
    try:
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == JANGSU_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JANGSU_PATH
        and query == [("menuCd", JANGSU_LIST_MENU)]
        and not parsed.fragment
    )


is_target = is_jangsu_education_target


def _raw_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _request(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _list_url(page: int = 1) -> str:
    query: list[tuple[str, str]] = [("menuCd", JANGSU_LIST_MENU)]
    if page > 1:
        query.append(("pageIndex", str(page)))
    return f"https://{JANGSU_HOST}{JANGSU_PATH}?{urlencode(query)}"


def _detail_url(identity: str) -> str:
    return (
        f"https://{JANGSU_HOST}{JANGSU_PATH}?"
        + urlencode((("menuCd", JANGSU_DETAIL_MENU), ("reUniqId", identity)))
    )


def _application_url(identity: str) -> str:
    return (
        f"https://{JANGSU_HOST}{JANGSU_PATH}?"
        + urlencode((("menuCd", JANGSU_APPLICATION_MENU), ("reUniqId", identity)))
    )


def _same_response_url(actual: str, expected: str) -> bool:
    try:
        left = urlparse(actual)
        right = urlparse(expected)
        return bool(
            left.scheme == right.scheme == "https"
            and (left.hostname or "").lower()
            == (right.hostname or "").lower()
            == JANGSU_HOST
            and left.port is None
            and right.port is None
            and left.username is None
            and left.password is None
            and left.path == right.path == JANGSU_PATH
            and parse_qsl(left.query, keep_blank_values=True, strict_parsing=True)
            == parse_qsl(right.query, keep_blank_values=True, strict_parsing=True)
            and not left.fragment
            and not right.fragment
        )
    except ValueError:
        return False


def _fetch_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[BeautifulSoup, int]:
    last_error: Optional[Exception] = None
    for attempt in range(1, JANGSU_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            if getattr(response, "status_code", None) != 200:
                raise JangsuContractError(
                    f"HTTP {getattr(response, 'status_code', None)} for {url}"
                )
            if getattr(response, "history", None):
                raise JangsuContractError(f"redirect is not allowed for {url}")
            response_url = _clean(getattr(response, "url", ""))
            if not _same_response_url(response_url, url):
                raise JangsuContractError(
                    f"response URL drift: expected {url}, received {response_url}"
                )
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", "")).encode("utf-8")
            if len(content) > JANGSU_MAX_HTML_BYTES:
                raise JangsuContractError(f"HTML exceeds byte limit for {url}")
            return BeautifulSoup(content, "html.parser"), attempt
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _options(select: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in select.find_all("option", recursive=False)
    )


def _validate_list_form(soup: BeautifulSoup) -> int:
    form = soup.select_one('form[name="listForm"]')
    if form is None:
        raise JangsuContractError("missing form[name=listForm]")
    if _clean(form.get("action")) != f"{JANGSU_PATH}?menuCd={JANGSU_LIST_MENU}":
        raise JangsuContractError("list form action drift")
    menu_input = form.select_one('input[name="menuCd"]')
    page_input = form.select_one('input[name="pageIndex"]')
    if (
        menu_input is None
        or _clean(menu_input.get("value")) != JANGSU_LIST_MENU
        or page_input is None
        or _clean(page_input.get("value")) != "1"
    ):
        raise JangsuContractError("list form hidden input drift")

    search_type = form.select('select[name="searchType"]')
    if len(search_type) != 2:
        raise JangsuContractError("duplicate searchType selector contract drift")
    if _options(search_type[0]) != (
        ("", "전체"),
        ("1", "접수중"),
        ("2", "강좌중"),
    ):
        raise JangsuContractError("status selector vocabulary drift")
    if _options(search_type[1]) != (
        ("RE_NAME", "강좌명"),
        ("GANGSA_NM", "강사명"),
        ("SANGSE_INFO", "상세내용"),
    ):
        raise JangsuContractError("keyword field selector vocabulary drift")
    category = form.select_one('select[name="bunya"]')
    if category is None or _options(category) != (
        ("", "분야선택"),
    ) + JANGSU_CATEGORY_FILTERS:
        raise JangsuContractError("category selector vocabulary drift")

    total_node = form.select_one(".totalTxt strong")
    if total_node is None:
        raise JangsuContractError("missing advertised source total")
    total_text = _clean(total_node.get_text(" ", strip=True)).replace(",", "")
    if not total_text.isdigit():
        raise JangsuContractError("advertised source total is not numeric")
    return int(total_text)


def _advertised_last(soup: BeautifulSoup) -> int:
    pager = soup.select_one("div.bbs_page")
    if pager is None:
        raise JangsuContractError("missing div.bbs_page")
    numbers: list[int] = []
    for node in pager.select("[onclick]"):
        match = _PAGE_CALL.search(_clean(node.get("onclick")))
        if match:
            numbers.append(int(match.group(1)))
    for anchor in pager.select("a[href]"):
        match = _PAGE_CALL.search(_clean(anchor.get("href")))
        if match:
            numbers.append(int(match.group(1)))
    current = pager.select_one("span.on")
    if current is None:
        raise JangsuContractError("missing current page marker")
    current_text = _clean(current.get_text(" ", strip=True))
    if not current_text.isdigit():
        raise JangsuContractError("malformed current page marker")
    numbers.append(int(current_text))
    return max(numbers)


def _title_and_branch(raw_title: str, identity: str) -> tuple[str, JangsuBranch]:
    match = _TITLE_BRANCH.fullmatch(_clean(raw_title))
    if not match:
        raise JangsuContractError(f"course {identity}: bracketed title/branch shape drift")
    branch_name, title = (_clean(value) for value in match.groups())
    branch = JANGSU_BRANCH_BY_NAME.get(branch_name)
    if branch is None:
        raise JangsuContractError(f"course {identity}: unknown official branch {branch_name}")
    return title, branch


def _parse_course_href(href: str, page: int) -> tuple[str, str]:
    try:
        parsed = urlparse(_clean(href))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JangsuContractError("malformed course detail href") from exc
    if parsed.scheme or parsed.netloc or parsed.path != JANGSU_PATH:
        raise JangsuContractError("course detail href escaped canonical path")
    values = dict(query)
    expected_keys = {"menuCd", "reUniqId"} | ({"pageIndex"} if page > 1 else set())
    if len(query) != len(expected_keys) or set(values) != expected_keys:
        raise JangsuContractError("course detail query shape drift")
    identity = values.get("reUniqId", "")
    if (
        values.get("menuCd") != JANGSU_DETAIL_MENU
        or not _IDENTITY.fullmatch(identity)
        or (page > 1 and values.get("pageIndex") != str(page))
    ):
        raise JangsuContractError("course detail identity/page binding drift")
    return identity, _detail_url(identity)


def _validate_list_application_href(href: str, identity: str, page: int) -> str:
    absolute = urljoin(JANGSU_CANONICAL_URL, _clean(href))
    try:
        parsed = urlparse(absolute)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JangsuContractError(f"course {identity}: malformed list application href") from exc
    values = dict(query)
    expected_keys = {"menuCd", "reUniqId"} | ({"pageIndex"} if page > 1 else set())
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JANGSU_HOST
        or parsed.port is not None
        or parsed.path != JANGSU_PATH
        or len(query) != len(expected_keys)
        or set(values) != expected_keys
        or values.get("menuCd") != JANGSU_APPLICATION_MENU
        or values.get("reUniqId") != identity
        or (page > 1 and values.get("pageIndex") != str(page))
        or parsed.fragment
    ):
        raise JangsuContractError(f"course {identity}: list application identity drift")
    return _application_url(identity)


def _parse_list_period(value: str, identity: str) -> dict[str, Any]:
    match = _LIST_PERIOD.fullmatch(_clean(value))
    if not match:
        raise JangsuContractError(f"course {identity}: list period shape drift")
    apply_start_text, apply_start_time, apply_end_text, event_start_text, event_end_text = (
        match.groups()
    )
    apply_start = date.fromisoformat(apply_start_text)
    apply_end = date.fromisoformat(apply_end_text)
    event_start = date.fromisoformat(event_start_text)
    event_end = date.fromisoformat(event_end_text)
    if apply_end < apply_start or event_end < event_start:
        raise JangsuContractError(f"course {identity}: reversed date range")
    return {
        "apply_start": apply_start,
        "apply_end": apply_end,
        "apply_start_time": apply_start_time,
        "event_start": event_start,
        "event_end": event_end,
    }


def _parse_list_row(node: Any, page: int, sequence: int) -> dict[str, Any]:
    cells = node.find_all("td", recursive=False)
    if len(cells) != 6:
        raise JangsuContractError("education list row no longer has six cells")
    sequence_text = _clean(cells[0].get_text(" ", strip=True))
    if not sequence_text.isdigit():
        raise JangsuContractError("source display sequence is not numeric")
    link = cells[3].select_one("a[href]")
    if link is None:
        raise JangsuContractError("course row lacks detail link")
    identity, detail_url = _parse_course_href(_clean(link.get("href")), page)
    raw_title = _clean(link.get_text(" ", strip=True))
    title, branch = _title_and_branch(raw_title, identity)

    category = _clean(cells[2].get_text(" ", strip=True))
    if category not in JANGSU_CATEGORIES:
        raise JangsuContractError(f"course {identity}: unknown category {category}")
    period = _parse_list_period(_clean(cells[4].get_text(" ", strip=True)), identity)
    waitlist_text = _clean(cells[5].get_text(" ", strip=True)).replace(",", "")
    if not waitlist_text.isdigit():
        raise JangsuContractError(f"course {identity}: non-numeric reserve capacity")

    status_span = cells[1].select_one("span")
    if status_span is None:
        raise JangsuContractError(f"course {identity}: missing source status")
    raw_status = _clean(status_span.get_text(" ", strip=True))
    if raw_status not in JANGSU_STATUS_MAP:
        raise JangsuContractError(f"course {identity}: unknown source status {raw_status}")
    if frozenset(status_span.get("class") or ()) != JANGSU_STATUS_CLASSES[raw_status]:
        raise JangsuContractError(f"course {identity}: source status class drift")
    status_anchor = cells[1].find("a", recursive=False)
    if raw_status == "접수중":
        if status_anchor is None:
            raise JangsuContractError(f"course {identity}: open row lacks application control")
        list_application_url = _validate_list_application_href(
            _clean(status_anchor.get("href")), identity, page
        )
    else:
        if status_anchor is not None:
            raise JangsuContractError(f"course {identity}: inactive row is actionable")
        list_application_url = ""

    return {
        "identity": identity,
        "detail_url": detail_url,
        "page": page,
        "sequence": sequence,
        "source_sequence": int(sequence_text),
        "raw_title": raw_title,
        "title": title,
        "branch": branch.name,
        "branch_code": branch.code,
        "category": category,
        "raw_status": raw_status,
        "status": JANGSU_STATUS_MAP[raw_status],
        "waitlist_total": int(waitlist_text),
        "list_application_url": list_application_url,
        **period,
    }


def _parse_list_page(
    soup: BeautifulSoup,
    requested_page: int,
) -> dict[str, Any]:
    total = _validate_list_form(soup)
    table = soup.select_one("div.board-list table.list01")
    if table is None:
        raise JangsuContractError("missing education table")
    headers = tuple(
        _clean(node.get_text(" ", strip=True)) for node in table.select("thead th")
    )
    if headers != JANGSU_LIST_HEADERS:
        raise JangsuContractError("education list header vocabulary drift")
    body_rows = table.select("tbody > tr")
    if not body_rows:
        raise JangsuContractError("education ledger unexpectedly returned no table rows")
    rows = [
        _parse_list_row(node, requested_page, sequence)
        for sequence, node in enumerate(body_rows, 1)
    ]
    if len(rows) > JANGSU_PAGE_SIZE:
        raise JangsuContractError("education page exceeds audited ten-row size")

    pager = soup.select_one("div.bbs_page")
    assert pager is not None
    current_node = pager.select_one("span.on")
    assert current_node is not None
    actual_page = int(_clean(current_node.get_text(" ", strip=True)))
    advertised_last = _advertised_last(soup)
    if actual_page > advertised_last:
        raise JangsuContractError("current page exceeds advertised last page")
    return {
        "requested_page": requested_page,
        "actual_page": actual_page,
        "advertised_last": advertised_last,
        "advertised_total": total,
        "rows": rows,
    }


def _page_signature(parsed: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(parsed["actual_page"]),
        int(parsed["advertised_last"]),
        int(parsed["advertised_total"]),
        tuple(
            (
                row["identity"],
                row["source_sequence"],
                row["raw_title"],
                row["category"],
                row["raw_status"],
                row["waitlist_total"],
                row["apply_start"].isoformat(),
                row["apply_end"].isoformat(),
                row["apply_start_time"],
                row["event_start"].isoformat(),
                row["event_end"].isoformat(),
                bool(row["list_application_url"]),
            )
            for row in parsed["rows"]
        ),
    )


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    view = soup.select_one("div.boardViewWrap")
    if view is None:
        raise JangsuContractError(f"course {identity}: missing detail container")
    fields: dict[str, str] = {}
    labels: list[str] = []
    for block in view.select("dl"):
        dt = block.find("dt", recursive=False)
        dd = block.find("dd", recursive=False)
        if dt is None or dd is None:
            raise JangsuContractError(f"course {identity}: malformed detail field")
        label = _clean(dt.get_text(" ", strip=True))
        if label in fields:
            raise JangsuContractError(f"course {identity}: repeated detail field {label}")
        labels.append(label)
        fields[label] = _clean(dd.get_text(" ", strip=True))
    if tuple(labels) != JANGSU_DETAIL_LABELS:
        raise JangsuContractError(f"course {identity}: detail field vocabulary/order drift")
    if view.select_one(".bdvCntWrap") is None:
        raise JangsuContractError(f"course {identity}: detail content boundary missing")
    return fields


def _parse_date_range(value: str, label: str, identity: str) -> tuple[date, date]:
    match = _DATE_RANGE.fullmatch(_clean(value))
    if not match:
        raise JangsuContractError(f"course {identity}: {label} date range shape drift")
    start, end = (date.fromisoformat(token) for token in match.groups())
    if end < start:
        raise JangsuContractError(f"course {identity}: reversed {label} date range")
    return start, end


def _validate_detail_control_href(
    href: str,
    *,
    menu: str,
    identity: str = "",
) -> str:
    absolute = urljoin(JANGSU_CANONICAL_URL, _clean(href))
    try:
        parsed = urlparse(absolute)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise JangsuContractError("malformed detail control href") from exc
    expected = (
        [("menuCd", menu), ("reUniqId", identity)]
        if identity
        else [("menuCd", menu)]
    )
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != JANGSU_HOST
        or parsed.port is not None
        or parsed.path != JANGSU_PATH
        or query != expected
        or parsed.fragment
    ):
        raise JangsuContractError(
            f"course {identity or 'list'}: detail control URL drift"
        )
    return _application_url(identity) if identity else JANGSU_CANONICAL_URL


def _detail_controls(
    soup: BeautifulSoup,
    identity: str,
    raw_status: str,
) -> tuple[bool, str]:
    controls = soup.select("div.btn-wrap.type01.tr > a")
    expected_count = 2 if raw_status == "접수중" else 1
    if len(controls) != expected_count:
        raise JangsuContractError(f"course {identity}: detail control count drift")
    back = controls[-1]
    if (
        _clean(back.get_text(" ", strip=True)) != "목록"
        or set(back.get("class") or ()) != {"list"}
    ):
        raise JangsuContractError(f"course {identity}: return-to-list control drift")
    _validate_detail_control_href(
        _clean(back.get("href")), menu=JANGSU_LIST_MENU
    )
    if raw_status != "접수중":
        return False, ""
    apply = controls[0]
    if (
        _clean(apply.get_text(" ", strip=True)) != "신청"
        or set(apply.get("class") or ()) != {"write"}
    ):
        raise JangsuContractError(f"course {identity}: application control drift")
    url = _validate_detail_control_href(
        _clean(apply.get("href")),
        menu=JANGSU_APPLICATION_MENU,
        identity=identity,
    )
    return True, url


def _money_amount(value: str) -> Optional[int]:
    text = _clean(value)
    if text in {"없음", "무료", "0", "0원"}:
        return 0
    compact = text.replace(",", "").replace("원", "").strip()
    return int(compact) if compact.isdigit() else None


def _parse_detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    title_node = soup.select_one("div.boardViewWrap .bdvTit")
    if title_node is None:
        raise JangsuContractError(f"course {identity}: missing detail title")
    raw_title = _clean(title_node.get_text(" ", strip=True))
    if raw_title != listed["raw_title"]:
        raise JangsuContractError(f"course {identity}: list/detail title drift")
    title, branch = _title_and_branch(raw_title, identity)
    if title != listed["title"] or branch.name != listed["branch"]:
        raise JangsuContractError(f"course {identity}: list/detail branch drift")

    fields = _detail_fields(soup, identity)
    if fields["분야"] != listed["category"]:
        raise JangsuContractError(f"course {identity}: list/detail category drift")
    if fields["상태"] != listed["raw_status"]:
        raise JangsuContractError(f"course {identity}: list/detail status drift")
    event_start, event_end = _parse_date_range(
        fields["교육기간"], "education", identity
    )
    apply_start, apply_end = _parse_date_range(
        fields["접수기간"], "application", identity
    )
    if event_start != listed["event_start"] or event_end != listed["event_end"]:
        raise JangsuContractError(f"course {identity}: list/detail education period drift")
    if apply_start != listed["apply_start"] or apply_end != listed["apply_end"]:
        raise JangsuContractError(f"course {identity}: list/detail application period drift")

    capacity_match = _CAPACITY.fullmatch(fields["수강인원"])
    if not capacity_match:
        raise JangsuContractError(f"course {identity}: capacity shape drift")
    capacity_current, capacity_total = (
        int(token.replace(",", "")) for token in capacity_match.groups()
    )
    raw_status = str(listed["raw_status"])
    status = str(listed["status"])
    if status == "SCHEDULED" and not cutoff < apply_start:
        raise JangsuContractError(f"course {identity}: scheduled status/date disagreement")
    if status == "OPEN" and not apply_start <= cutoff <= apply_end:
        raise JangsuContractError(f"course {identity}: open status/date disagreement")
    if raw_status == "강좌중" and not event_start <= cutoff <= event_end:
        raise JangsuContractError(f"course {identity}: in-progress status/date disagreement")

    control_present, application_url = _detail_controls(
        soup, identity, raw_status
    )
    if control_present != bool(listed["list_application_url"]):
        raise JangsuContractError(f"course {identity}: list/detail application control drift")
    if application_url and application_url != listed["list_application_url"]:
        raise JangsuContractError(f"course {identity}: application URL identity drift")

    schedule = fields["강의시간"]
    target = fields["대상"]
    venue = fields["교육장소"]
    facility = fields["교육시설"]
    method = fields["접수방법"]
    if not schedule or not target or not venue or method != "온라인":
        raise JangsuContractError(
            f"course {identity}: schedule/target/venue/application method drift"
        )
    safe_values = (title, branch.name, schedule, target, venue, facility)
    if any(
        _PHONE.search(value) or _EMAIL.search(value) or _RESIDENT_ID.search(value)
        for value in safe_values
    ):
        raise JangsuContractError(f"course {identity}: allowlisted field contains PII")

    detail_url = str(listed["detail_url"])
    event_period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    return {
        "provider": JANGSU_PROVIDER,
        "provider_course_id": f"{JANGSU_PROVIDER}:gjre:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch.name,
        "branch_code": branch.code,
        "branch_url": JANGSU_CANONICAL_URL,
        "preserve_branch": True,
        "category": str(listed["category"]),
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if status == "OPEN" else "INFO_ONLY",
        "application_method": "온라인",
        "application_methods": ["온라인"],
        "reservation_available": control_present,
        "status": status,
        "raw_status": raw_status,
        "fee": fields["수강료"],
        "fee_amount": _money_amount(fields["수강료"]),
        "material_fee": fields["교재비용"],
        "material_fee_amount": _money_amount(fields["교재비용"]),
        "period": event_period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - capacity_current, 0),
        "waitlist_total": int(listed["waitlist_total"]),
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "room": venue,
        "facility_name": facility,
        "address": "",
        "venue_address": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": JANGSU_PARSER,
        "municipality_code": JANGSU_MUNICIPALITY_CODE,
        "municipality_full_name": JANGSU_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_sequence": int(listed["source_sequence"]),
            "source_status": raw_status,
            "source_category": str(listed["category"]),
            "source_branch": branch.name,
            "source_apply_period": apply_period,
            "source_education_period": event_period,
            "source_apply_start_time": str(listed["apply_start_time"]),
            "source_waitlist_total": int(listed["waitlist_total"]),
            "source_schedule": schedule,
            "source_target": target,
            "source_facility": facility,
            "source_venue": venue,
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_verified": True,
            "application_endpoint_fetched": False,
            "reservation_lookup_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "discarded_detail_fields": list(JANGSU_DISCARDED_DETAIL_FIELDS),
            "address_policy": "venue_name_only_no_verified_street_address",
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_ROW_KEYS:
        errors.append("forbidden PII/free-text key")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields allowlist exceeded")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "branch_url"}
        }
    )
    if _PHONE.search(payload) or _EMAIL.search(payload) or _RESIDENT_ID.search(payload):
        errors.append("PII persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail persisted")
    if row.get("address") or row.get("venue_address"):
        errors.append("unverified street address persisted")
    return errors


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = str(row["provider_course_id"])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("Asia/Seoul")).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _clean(row.get("title")).casefold(),
        _clean(row.get("branch")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("venue_name")),
    )


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": JANGSU_MUNICIPALITY_CODE,
        "municipality_full_name": JANGSU_MUNICIPALITY_NAME,
        "owner_provider": JANGSU_PROVIDER,
        "canonical_provider": JANGSU_PROVIDER,
        "canonical_candidate_id": JANGSU_CANONICAL_CANDIDATE_ID,
        "review_candidate_id": JANGSU_REVIEW_DETAIL_CANDIDATE_ID,
        "canonical_url": JANGSU_CANONICAL_URL,
        "candidate_audit": {
            key: dict(value) for key, value in JANGSU_CANDIDATE_AUDIT.items()
        },
        "provider_decision": (
            "keep incumbent provider; retarget single-detail review candidate "
            "to complete canonical list"
        ),
        "owner_boundaries": [dict(item) for item in JANGSU_OWNER_BOUNDARIES],
        "ownership_scope": JANGSU_OWNERSHIP_SCOPE,
        "parser": JANGSU_PARSER,
        "page_size": JANGSU_PAGE_SIZE,
        "pagination_boundary_mode": "advertised_last_plus_clamped_overflow",
        "recommended_max_pages": JANGSU_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": JANGSU_RECOMMENDED_DETAIL_LIMIT,
        "recommended_max_workers": JANGSU_RECOMMENDED_MAX_WORKERS,
        "recommended_timeout_seconds": 30,
        "fetch_attempts": JANGSU_FETCH_ATTEMPTS,
        "max_html_bytes": JANGSU_MAX_HTML_BYTES,
        "live_audit_baseline": dict(JANGSU_LIVE_AUDIT_BASELINE),
        "address_policy": "detail venue name only; no verified street address",
        "pii_policy": (
            "discard manager/contact/instructor/introduction/content/files/images "
            "and never fetch application or reservation lookup"
        ),
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "application_endpoints_called": 0,
        "reservation_lookup_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "advertised_total": 0,
        "advertised_last_page": 0,
        "data_pages": 0,
        "page_counts": [],
        "overflow_page": 0,
        "overflow_actual_page": 0,
        "overflow_clamp_verified": False,
        "page1_rechecked": False,
        "last_page_rechecked": False,
        "overflow_rechecked": False,
        "boundary_rechecks": 0,
        "source_rows": 0,
        "source_sequence_duplicate_count": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "excluded_title_count": 0,
        "returned_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "privacy_violations": 0,
        "semantic_duplicate_count": 0,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "configured_collection_error": "",
    }


def _fetch_parsed_page(
    session: Any,
    page: int,
    timeout: int,
    fetcher: Fetcher,
) -> tuple[dict[str, Any], int]:
    soup, attempts = _fetch_soup(session, _list_url(page), timeout, fetcher)
    return _parse_list_page(soup, page), attempts


def collect_jangsu_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = JANGSU_RECOMMENDED_MAX_PAGES,
    detail_limit: int = JANGSU_RECOMMENDED_DETAIL_LIMIT,
    max_workers: int = JANGSU_RECOMMENDED_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Jangsu education snapshot."""

    meta = _initial_meta()
    if not is_jangsu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match exact incumbent Jangsu education owner"
        )
        return [], JANGSU_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], JANGSU_PARSER, meta
        session_factory = _raw_session
    try:
        cutoff = _today(today)
        if any(
            isinstance(value, bool) or int(value) < 1
            for value in (timeout, max_pages, max_workers)
        ):
            raise ValueError("timeout, max_pages, and max_workers must be positive integers")
        if isinstance(detail_limit, bool) or int(detail_limit) < 0:
            raise ValueError("detail_limit must be a non-negative integer")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JANGSU_PARSER, meta

    current_fetcher = fetcher or _request
    main_session = session_factory()
    try:
        first, attempts = _fetch_parsed_page(
            main_session, 1, int(timeout), current_fetcher
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        if int(first["actual_page"]) != 1:
            raise JangsuContractError("canonical first page did not render page one")
        advertised_last = int(first["advertised_last"])
        advertised_total = int(first["advertised_total"])
        if advertised_last > int(max_pages):
            meta["source_cap_reached"] = True
            raise JangsuContractError(
                f"advertised last page {advertised_last} exceeds max_pages {max_pages}"
            )

        pages: dict[int, dict[str, Any]] = {1: first}

        def fetch_list_worker(page: int) -> tuple[int, dict[str, Any], int]:
            worker_session = session_factory()
            try:
                parsed, worker_attempts = _fetch_parsed_page(
                    worker_session, page, int(timeout), current_fetcher
                )
                return page, parsed, worker_attempts
            finally:
                close_worker = getattr(worker_session, "close", None)
                if callable(close_worker):
                    close_worker()

        if advertised_last > 1:
            with ThreadPoolExecutor(
                max_workers=min(int(max_workers), advertised_last - 1)
            ) as executor:
                futures = [
                    executor.submit(fetch_list_worker, page)
                    for page in range(2, advertised_last + 1)
                ]
                for future in as_completed(futures):
                    page, parsed, worker_attempts = future.result()
                    pages[page] = parsed
                    meta["source_requests"] += 1
                    meta["list_requests"] += 1
                    meta["request_attempts"] += worker_attempts

        ordered_pages = [pages[page] for page in range(1, advertised_last + 1)]
        for page_number, parsed in enumerate(ordered_pages, 1):
            if (
                int(parsed["actual_page"]) != page_number
                or int(parsed["advertised_last"]) != advertised_last
                or int(parsed["advertised_total"]) != advertised_total
            ):
                raise JangsuContractError("advertised pagination contract drift")
        for parsed in ordered_pages[:-1]:
            if len(parsed["rows"]) != JANGSU_PAGE_SIZE:
                raise JangsuContractError("non-final page is not a full ten-row page")
        if not 1 <= len(ordered_pages[-1]["rows"]) <= JANGSU_PAGE_SIZE:
            raise JangsuContractError("final page size drift")
        calculated_total = (
            (advertised_last - 1) * JANGSU_PAGE_SIZE
            + len(ordered_pages[-1]["rows"])
        )
        if calculated_total != advertised_total:
            raise JangsuContractError("advertised total does not reconcile with page boundary")

        overflow_page = advertised_last + 1
        overflow, attempts = _fetch_parsed_page(
            main_session, overflow_page, int(timeout), current_fetcher
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        if (
            int(overflow["actual_page"]) != advertised_last
            or _page_signature(overflow) != _page_signature(ordered_pages[-1])
        ):
            raise JangsuContractError("post-boundary page did not clamp exactly to last page")

        listed = [row for parsed in ordered_pages for row in parsed["rows"]]
        identities = [str(row["identity"]) for row in listed]
        if len(identities) != advertised_total or len(identities) != len(set(identities)):
            raise JangsuContractError("GJRE identity set is incomplete or duplicated")
        source_sequences = [int(row["source_sequence"]) for row in listed]
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "advertised_total": advertised_total,
                "advertised_last_page": advertised_last,
                "data_pages": advertised_last,
                "nonempty_pages": advertised_last,
                "page_counts": [len(parsed["rows"]) for parsed in ordered_pages],
                "overflow_page": overflow_page,
                "overflow_actual_page": int(overflow["actual_page"]),
                "overflow_clamp_verified": True,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_sequence_duplicate_count": len(source_sequences)
                - len(set(source_sequences)),
                "source_status_counts": dict(
                    Counter(str(row["raw_status"]) for row in listed)
                ),
                "source_category_counts": dict(
                    Counter(str(row["category"]) for row in listed)
                ),
                "source_branch_counts": dict(
                    Counter(str(row["branch"]) for row in listed)
                ),
                "source_identity_numeric_min": min(
                    int(identity.removeprefix("GJRE")) for identity in identities
                ),
                "source_identity_numeric_max": max(
                    int(identity.removeprefix("GJRE")) for identity in identities
                ),
                "pagination_complete": True,
            }
        )

        current_all = [row for row in listed if row["event_end"] >= cutoff]
        excluded = [row for row in current_all if _EXCLUDED_TITLE.search(str(row["title"]))]
        current = [row for row in current_all if row not in excluded]
        meta.update(
            {
                "current_source_count": len(current_all),
                "expired_source_count": len(listed) - len(current_all),
                "excluded_title_count": len(excluded),
            }
        )
        if len(current) > int(detail_limit):
            meta["source_cap_reached"] = True
            raise JangsuContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )

        def fetch_detail_worker(
            item: Mapping[str, Any],
        ) -> tuple[dict[str, Any], int]:
            worker_session = session_factory()
            try:
                soup, worker_attempts = _fetch_soup(
                    worker_session,
                    str(item["detail_url"]),
                    int(timeout),
                    current_fetcher,
                )
                return _parse_detail(item, soup, cutoff), worker_attempts
            finally:
                close_worker = getattr(worker_session, "close", None)
                if callable(close_worker):
                    close_worker()

        detail_results: list[tuple[dict[str, Any], int]] = []
        if current:
            with ThreadPoolExecutor(
                max_workers=min(int(max_workers), len(current))
            ) as executor:
                futures = [
                    executor.submit(fetch_detail_worker, item) for item in current
                ]
                for future in as_completed(futures):
                    row, worker_attempts = future.result()
                    detail_results.append((row, worker_attempts))
                    meta["source_requests"] += 1
                    meta["detail_pages"] += 1
                    meta["request_attempts"] += worker_attempts
        rows = [row for row, _ in detail_results]

        first_recheck, attempts = _fetch_parsed_page(
            main_session, 1, int(timeout), current_fetcher
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        meta["boundary_rechecks"] += 1
        if _page_signature(first_recheck) != _page_signature(first):
            raise JangsuContractError("page-one stability recheck failed")
        meta["page1_rechecked"] = True

        last_recheck, attempts = _fetch_parsed_page(
            main_session, advertised_last, int(timeout), current_fetcher
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        meta["boundary_rechecks"] += 1
        if _page_signature(last_recheck) != _page_signature(ordered_pages[-1]):
            raise JangsuContractError("last-page stability recheck failed")
        meta["last_page_rechecked"] = True

        overflow_recheck, attempts = _fetch_parsed_page(
            main_session, overflow_page, int(timeout), current_fetcher
        )
        meta["source_requests"] += 1
        meta["list_requests"] += 1
        meta["request_attempts"] += attempts
        meta["boundary_rechecks"] += 1
        if _page_signature(overflow_recheck) != _page_signature(overflow):
            raise JangsuContractError("overflow-clamp stability recheck failed")
        meta["overflow_rechecked"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{JANGSU_PROVIDER}:gjre:{item['identity']}" for item in current
        }
        if len(rows) != len(current) or {
            str(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise JangsuContractError("dedupe changed the current GJRE identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise JangsuContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(_semantic_key(row) for row in rows)
        semantic_duplicates = sum(count - 1 for count in semantic_counts.values() if count > 1)
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise JangsuContractError("semantic duplicate current courses detected")

        meta.update(
            {
                "returned_count": len(rows),
                "details_complete": meta["detail_pages"] == len(current),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "raw_status_counts": dict(Counter(str(row["raw_status"]) for row in rows)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "category_counts": dict(Counter(str(row["category"]) for row in rows)),
                "target_counts": dict(Counter(str(row["target"]) for row in rows)),
                "venue_counts": dict(Counter(str(row["venue_name"]) for row in rows)),
                "application_control_count": sum(
                    bool(row["raw_fields"]["application_control_present"]) for row in rows
                ),
                "actionable_application_count": sum(
                    bool(row["application_url"]) for row in rows
                ),
                "no_current_data": not rows,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
            }
        )
        return rows, JANGSU_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], JANGSU_PARSER, meta
    finally:
        close = getattr(main_session, "close", None)
        if callable(close):
            close()


collect = collect_jangsu_education
