"""Fail-closed collector for Andong City's integrated education catalogue.

``search.do?mId=0101000000`` is the authoritative city lifelong-learning
catalogue.  It combines the learning centre, on-demand road classes, citizen
instructors, invited lectures, universities, municipal departments, the
museum, library imports, and eup/myeon/dong operators.  The coverage-review
URL with ``eduGroupList=6`` is only the adult-age filter of this owner.

The Andong municipal library and Andong Facilities Corporation operate
independent identity/application systems and are deliberately not merged into
this provider.  Applicant endpoints are never requested.  Current/future
rows are verified against the public modal detail, including its hidden
identity and visible application control, before any atomic snapshot is
returned.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from bs4 import BeautifulSoup


ANDONG_PROVIDER = "MUNI_WWW_ANDONG_GO_KR_1430676F"
ANDONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_97E64D7E3AF5"
ANDONG_REVIEW_FILTER_PROVIDER = "MUNI_WWW_ANDONG_GO_KR_5F061053"
ANDONG_REVIEW_FILTER_CANDIDATE_ID = "MUNI_IR_B789BE4C04EF"
ANDONG_MUNICIPALITY_CODE = "4717000000"
ANDONG_MUNICIPALITY_NAME = "경상북도 안동시"
ANDONG_HOST = "www.andong.go.kr"
ANDONG_LIST_PATH = "/edu/forever/lecture/search.do"
ANDONG_DETAIL_PATH = "/edu/forever/lecture/totalDetail.do"
ANDONG_CANONICAL_URL = f"https://{ANDONG_HOST}{ANDONG_LIST_PATH}?mId=0101000000"
ANDONG_REVIEW_FILTER_URL = f"https://{ANDONG_HOST}{ANDONG_LIST_PATH}?mId=0101000000&eduGroupList=6&detailSearchYn=true"
ANDONG_PAGE_SIZE = 24
ANDONG_MAX_HTML_BYTES = 3_000_000
ANDONG_MAX_WORKERS = 10

# The official ledger contains one historic upstream typo: its displayed end
# date precedes its start date.  Keep the exception identity- and value-bound
# so a new malformed/current record still fails closed, while this 2024 closed
# row cannot disable the complete owner snapshot forever.
_KNOWN_REVERSED_ARCHIVED_PERIODS = {
    ("6", "286", "2024-05-04 ~ 2024-03-18"): (date(2024, 3, 18), date(2024, 5, 4)),
}
_RETIRED_EXTERNAL_OWNER_IDENTITIES = {
    ("47", "옥동행복학습센터"),
    ("48", "용상동행복학습센터"),
    ("49", "용상동행복학습센터"),
    ("50", "용상동행복학습센터"),
}
ANDONG_PARSER = (
    "andong_official_integrated_lifelong_education_catalogue+"
    "all_24_row_pages+exact_empty_post_last+stable_first_last+"
    "institution_and_filter_registry+fixed_and_on_demand_current_partition+"
    "current_candidate_details+identity_bound_application_controls+"
    "experience_exclusion+separate_owner_boundaries+pii_allowlist"
)

ANDONG_LIBRARY_CULTURE_URL = "https://lib.andong.go.kr/andonglibrary/module/teach/index.do?menu_idx=362&searchCate1=16"
ANDONG_LIBRARY_CULTURE_PROVIDER = "MUNI_LIB_ANDONG_GO_KR_6B34DA7C"
ANDONG_LIBRARY_CULTURE_CANDIDATE_ID = "MUNI_IR_7297F22AB354"
ANDONG_LIBRARY_EVENT_URL = "https://lib.andong.go.kr/andonglibrary/module/teach/index.do?menu_idx=368&searchCate1=23"
ANDONG_LIBRARY_EVENT_PROVIDER = "MUNI_LIB_ANDONG_GO_KR_F96F2899"
ANDONG_LIBRARY_EVENT_CANDIDATE_ID = "MUNI_IR_7D913AB00A9A"
ANDONG_LIBRARY_HOST = "lib.andong.go.kr"
ANDONG_LIBRARY_LIST_PATH = "/andonglibrary/module/teach/index.do"
ANDONG_LIBRARY_DETAIL_PATH = "/andonglibrary/module/teach/detail.do"
ANDONG_LIBRARY_APPLICATION_PATH = "/andonglibrary/module/teach/student/edit.do"
ANDONG_LIBRARY_PARSER = (
    "andong_municipal_library_teach_ledgers+exact_menu_and_category_identity+"
    "unpaginated_complete_boundary_recheck+current_future_public_details+"
    "branch_registry+identity_bound_application_controls+"
    "performance_subscription_and_experience_exclusion+pii_allowlist"
)

ANDONG_DISCOVERY_AUDIT: dict[str, Any] = {
    "canonical_owner": {
        "url": ANDONG_CANONICAL_URL,
        "decision": "include_complete_integrated_lifelong_owner",
        "live_source_scope": "learning centre plus all registered external institutions",
    },
    "coverage_review_candidate": {
        "url": ANDONG_REVIEW_FILTER_URL,
        "decision": "exclude_adult_age_filter_of_canonical_owner",
        "audited_live_rows": 659,
    },
    "lifelong_home_and_program_menus": {
        "urls": (
            "https://www.andong.go.kr/edu/main.do",
            "https://www.andong.go.kr/edu/forever/lecture/list.do?category=10&mId=0301010100",
            "https://www.andong.go.kr/edu/forever/lecture/list.do?category=20&mId=0301010200",
            "https://www.andong.go.kr/edu/forever/lecture/list.do?category=30&mId=0301010300",
        ),
        "decision": "exclude_recommendation_and_learning_centre_subsets",
    },
    "municipal_library": {
        "url": ANDONG_LIBRARY_CULTURE_URL,
        "event_url": ANDONG_LIBRARY_EVENT_URL,
        "decision": "separate_owner_not_merged",
        "reason": "independent host, teach identity, branch registry, membership and applicant workflow",
    },
    "facilities_corporation": {
        "url": "https://www.andongsisul.or.kr",
        "decision": "separate_public_corporation_owner_not_merged",
        "reason": "youth-centre and sports course reservation identities are outside the city lifelong ledger",
    },
    "portal_facility_reservations": {
        "url": "https://www.andong.go.kr/portal/gym/apply/calendar.do?mId=0612050200",
        "decision": "exclude_non_education_facility_inventory",
    },
}

ANDONG_OWNER_BOUNDARY_AUDIT: dict[str, dict[str, str]] = {
    "integrated_lifelong_search": {
        "decision": "canonical",
        "reason": "one complete catalogue owns all five eduType identity namespaces",
    },
    "adult_and_other_search_filters": {
        "decision": "duplicate_subset",
        "reason": "same (eduType, idx) identities with query filters",
    },
    "learning_centre_roadclass_citizen_menus": {
        "decision": "duplicate_subset",
        "reason": "service menus reuse identities and detail/application routes in the integrated catalogue",
    },
    "municipal_library_teach": {
        "decision": "separate_owner_collect_event_education",
        "reason": "library teach_idx ledger is not an integrated-search mirror; culture and event ledgers retain separate source URLs",
    },
    "andongsisul_courses": {
        "decision": "separate_owner",
        "reason": "municipal corporation reservation platform",
    },
    "gym_waterpark_and_facilities": {
        "decision": "exclude_non_education",
        "reason": "time-slot/facility inventory rather than education courses",
    },
}

ANDONG_PII_FIELDS_NEVER_PERSISTED = (
    "강사명",
    "교육내용",
    "강의계획서",
    "문의처",
    "활동사항",
    "자격사항",
    "신청자명",
    "생년월일",
    "주소",
    "전화번호",
    "이메일",
)

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class AndongContractError(ValueError):
    """Raised when the audited official Andong source contract changes."""


@dataclass(frozen=True)
class _Page:
    requested: int
    observed: int
    last: int
    total: int
    rows: tuple[dict[str, Any], ...]
    registry: tuple[Any, ...]
    structural_empty: bool


@dataclass(frozen=True)
class _LibraryLedger:
    menu_idx: str
    large_category_idx: str
    rows: tuple[dict[str, Any], ...]
    branch_registry: tuple[tuple[str, str], ...]
    structural_empty: bool


_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_TOTAL = re.compile(r"^전체\s*([\d,]+)\s*건\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)$")
_CONTROL = re.compile(r"^fn_popup_open_totalLecture\(([1-9]\d*),([12346]),'Y'\);$")
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CAPACITY = re.compile(r"^(\d[\d,]*)(?:명)?(?:\s*/\s*(\d[\d,]*)명)?")
_REGISTRATION = re.compile(r"^(\d[\d,]*)명\s*/\s*(\d[\d,]*)명\s*/\s*(\d[\d,]*)명\s*\(신청/확정/후보\)$")
_LIBRARY_COUNTS = re.compile(r"^(\d[\d,]*)\s*/\s*(\d[\d,]*)\s+(\d[\d,]*)\s*/\s*(\d[\d,]*)$")
_LIBRARY_DETAIL_COUNTS = re.compile(r"^(\d[\d,]*)\s*명\s*/\s*(\d[\d,]*)\s*명$")

_TYPE_OWNER = {
    "1": "학습관교육",
    "2": "길거리교실",
    "3": "시민강사",
    "4": "명사초청",
}
_DETAIL_OWNER = {
    "1": "학습관",
    "2": "길거리 교실",
    "3": "시민강사",
    "4": "명사초청",
}
_SOURCE_STATUS = {
    "접수중": "OPEN",
    "수업가능": "OPEN",
    "대기": "SCHEDULED",
    "마감": "CLOSED",
    "운영종료": "CLOSED",
}
_DETAIL_STATUS = {
    "접수중": "OPEN",
    "수업가능": "OPEN",
    "접수대기": "SCHEDULED",
    "대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "마감": "CLOSED",
    "운영종료": "CLOSED",
}
_ACTIVE_SOURCE = {"접수중", "수업가능", "대기"}
_LIST_FIELDSETS = {
    ("교육기간", "교육시간"),
    ("강사명",),
    ("강연일", "강연시간"),
}
_DETAIL_SAFE_FIELDS = {
    "교육분야",
    "교육대상",
    "교육기간",
    "교육시간",
    "수강료",
    "재료비(기타비용)",
    "교육장소",
    "모집형태",
    "1차접수기간",
    "모집정원",
    "2차접수기간",
    "등록현황",
}
_DETAIL_DISCARDED_FIELDS = {"교육내용", "강의계획서", "문의처"}
_DETAIL_REQUIRED_FIELDS = {
    "교육분야",
    "교육대상",
    "교육기간",
    "교육시간",
    "수강료",
    "재료비(기타비용)",
    "교육장소",
    "모집형태",
    "1차접수기간",
    "모집정원",
}
_CATEGORY_MID = {
    "10": "0301010100",
    "20": "0301010200",
    "30": "0301010300",
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "edu_type",
        "list_page",
        "source_owner",
        "source_status",
        "detail_status",
        "source_list_fieldset",
        "source_education_period",
        "source_schedule",
        "source_apply_period",
        "source_second_apply_period",
        "source_category",
        "source_target",
        "source_venue",
        "source_recruitment",
        "source_category_code",
        "branch_basis",
        "current_basis",
        "detail_verified",
        "application_control_present",
        "education_scope_verified",
        "experience_scope_verified",
        "service_family",
        "ledger_menu_idx",
        "large_category_idx",
        "group_idx",
        "category_idx",
        "teach_idx",
        "list_sequence",
        "source_branch",
        "source_period",
        "source_recruitment_method",
        "education_scope_basis",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "phone",
        "email",
        "contact",
        "instructor",
        "attachments",
        "attachment_urls",
        "detail_description",
        "source_html",
        "raw_html",
        "image_url",
    }
)


def _clean(value: Any) -> str:
    return _SPACE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlparse(url).query, keep_blank_values=True, strict_parsing=True)


def _is_andong_integrated_target(target: Any) -> bool:
    if _clean(_value(target, "provider")) != ANDONG_PROVIDER:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = _query(parsed.geturl())
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ANDONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ANDONG_LIST_PATH
        and query == [("mId", "0101000000")]
        and not parsed.fragment
    )


def _is_andong_library_target(target: Any) -> bool:
    provider = _clean(_value(target, "provider"))
    expected = {
        ANDONG_LIBRARY_CULTURE_PROVIDER: [("menu_idx", "362"), ("searchCate1", "16")],
        ANDONG_LIBRARY_EVENT_PROVIDER: [("menu_idx", "368"), ("searchCate1", "23")],
    }.get(provider)
    if expected is None:
        return False
    parsed = urlparse(_clean(_value(target, "url")))
    try:
        port = parsed.port
        query = _query(parsed.geturl())
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ANDONG_LIBRARY_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == ANDONG_LIBRARY_LIST_PATH
        and query == expected
        and not parsed.fragment
    )


def is_andong_education_target(target: Any) -> bool:
    return _is_andong_integrated_target(target) or _is_andong_library_target(target)


is_target = is_andong_education_target


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MooncenMunicipalCrawler/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def andong_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"https://{ANDONG_HOST}{ANDONG_LIST_PATH}?" + urlencode(
        (
            ("mId", "0101000000"),
            ("recordCountPerPage", str(ANDONG_PAGE_SIZE)),
            ("currentPageNo", str(page)),
        )
    )


def andong_detail_url(identity: str, edu_type: str) -> str:
    if _IDENTITY.fullmatch(str(identity)) is None or str(edu_type) not in {"1", "2", "3", "4", "6"}:
        raise ValueError("invalid Andong education identity")
    return f"https://{ANDONG_HOST}{ANDONG_DETAIL_PATH}?" + urlencode(
        (("mId", "0101000000"), ("idx", str(identity)), ("eduType", str(edu_type)))
    )


def andong_application_url(identity: str, edu_type: str, category: str = "") -> str:
    if _IDENTITY.fullmatch(str(identity)) is None:
        raise ValueError("invalid Andong education identity")
    if edu_type == "1":
        mid, path = _CATEGORY_MID.get(category, ""), "/edu/forever/receipt/write.do"
    elif edu_type == "2":
        mid, path = "0303020000", "/edu/roadclass/app/write.do"
    elif edu_type == "3":
        mid, path = "0304020000", "/edu/citizen/app/write.do"
    else:
        raise ValueError("external application uses its audited selectedUrl")
    if not mid:
        raise ValueError("invalid Andong learning-centre category")
    return f"https://{ANDONG_HOST}{path}?" + urlencode((("lectureIdx", str(identity)), ("mId", mid)))


def _allowed_request_url(url: str) -> bool:
    parsed = urlparse(url)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == ANDONG_HOST
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.fragment
    ):
        return False
    try:
        pairs = _query(url)
    except ValueError:
        return False
    if parsed.path == ANDONG_LIST_PATH:
        return bool(
            len(pairs) == 3
            and pairs[0] == ("mId", "0101000000")
            and pairs[1] == ("recordCountPerPage", str(ANDONG_PAGE_SIZE))
            and pairs[2][0] == "currentPageNo"
            and _IDENTITY.fullmatch(pairs[2][1])
        )
    return bool(
        parsed.path == ANDONG_DETAIL_PATH
        and len(pairs) == 3
        and pairs[0] == ("mId", "0101000000")
        and pairs[1][0] == "idx"
        and _IDENTITY.fullmatch(pairs[1][1])
        and pairs[2][0] == "eduType"
        and pairs[2][1] in {"1", "2", "3", "4", "6"}
    )


def _request_soup(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    if not _allowed_request_url(url):
        raise AndongContractError("request left the audited Andong list/detail contract")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        session = session_factory()
        try:
            response = fetcher(session, url, timeout)
            status = int(getattr(response, "status_code", 200))
            if status != 200:
                raise AndongContractError(f"unexpected HTTP status {status}")
            headers = getattr(response, "headers", {}) or {}
            if headers.get("Location"):
                raise AndongContractError("redirect response is not accepted")
            final_url = _clean(getattr(response, "url", url)) or url
            if not _allowed_request_url(final_url) or _query(final_url) != _query(url):
                raise AndongContractError("response URL changed")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not content or len(content) > ANDONG_MAX_HTML_BYTES:
                raise AndongContractError("empty or oversized official HTML response")
            return BeautifulSoup(content, "lxml", from_encoding="utf-8")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


def _option_registry(soup: BeautifulSoup, name: str) -> tuple[tuple[str, str], ...]:
    selects = soup.select(f"select[name='{name}']")
    if len(selects) != 1:
        raise AndongContractError(f"{name} registry control changed")
    values = tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in selects[0].select("option")
    )
    if not values or len(values) != len(set(values)):
        raise AndongContractError(f"{name} registry is empty or duplicated")
    return values


def _input_registry(soup: BeautifulSoup, name: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for node in soup.select(f"input[name='{name}'][value]"):
        identity = _clean(node.get("id"))
        labels = soup.select(f"label[for='{identity}']") if identity else []
        if len(labels) != 1:
            raise AndongContractError(f"{name} filter label changed")
        result.append((_clean(node.get("value")), _clean(labels[0].get_text(" ", strip=True))))
    if not result or len(result) != len(set(result)):
        raise AndongContractError(f"{name} filter registry changed")
    return tuple(result)


def _filter_registry(soup: BeautifulSoup) -> tuple[Any, ...]:
    edu_types = _option_registry(soup, "eduType")
    if edu_types != (
        ("0", "전체"),
        ("1", "학습관교육"),
        ("2", "길거리교실"),
        ("3", "시민강사"),
        ("4", "명사초청"),
    ):
        raise AndongContractError("education-type registry changed")
    orgs = _option_registry(soup, "orgIdx")
    if orgs[0] != ("9999", "전체") or any(_IDENTITY.fullmatch(code) is None or not name for code, name in orgs[1:]):
        raise AndongContractError("external institution registry changed")
    expected_inputs = {
        "typeList": {"10", "20", "30", "40", "50", "60", "70", "80", "90", "100", "111", "160", "300"},
        "lectureTimeList": {"1", "2", "3", "4", "5"},
        "eduDayList": {"1", "2", "3", "4", "5", "6", "7"},
        "eduGroupList": {"1", "2", "3", "4", "5", "6", "7"},
        "costTypeList": {"P", "F"},
        "recruitmentTypeList": {"1", "2"},
        "stateList": {"1", "2", "3", "4"},
    }
    input_values: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for name, expected in expected_inputs.items():
        registry = _input_registry(soup, name)
        if {value for value, _label in registry} != expected:
            raise AndongContractError(f"{name} filter values changed")
        input_values.append((name, registry))
    record_counts = _option_registry(soup, "recordCountPerPage")
    if tuple(value for value, _label in record_counts) != ("12", "16", "24"):
        raise AndongContractError("page-size registry changed")
    return (edu_types, orgs, tuple(input_values), record_counts)


def _dates(value: Any) -> tuple[date, ...]:
    return tuple(date(int(year), int(month), int(day)) for year, month, day in _DATE.findall(_clean(value)))


def _date_range(value: Any, context: str) -> tuple[date, date]:
    values = _dates(value)
    if len(values) == 1:
        return values[0], values[0]
    if len(values) != 2 or values[1] < values[0]:
        raise AndongContractError(f"{context}: invalid date range")
    return values[0], values[1]


def _list_fields(node: Any, identity: str) -> dict[str, str]:
    result: dict[str, str] = {}
    labels: list[str] = []
    for item in node.select("ul.detail > li"):
        strong = item.select_one("strong")
        if strong is None:
            raise AndongContractError(f"course {identity}: list field label missing")
        label = _clean(strong.get_text(" ", strip=True))
        labels.append(label)
        if label == "강사명":
            # Instructor names are deliberately never read into a Python string.
            result[label] = ""
            continue
        text = _clean(item.get_text(" ", strip=True))
        if not text.startswith(label):
            raise AndongContractError(f"course {identity}: list field shape changed")
        value = text[len(label) :].strip()
        if _PHONE.search(value) or _EMAIL.search(value):
            raise AndongContractError(f"course {identity}: contact-like list field refused")
        result[label] = value
    if tuple(labels) not in _LIST_FIELDSETS:
        raise AndongContractError(f"course {identity}: list fieldset changed")
    return result


def _parse_page(soup: BeautifulSoup, requested: int) -> _Page:
    totals = soup.select(".page-num .total")
    page_inputs = soup.select("input[name='currentPageNo']")
    if len(totals) != 1 or len(page_inputs) != 1:
        raise AndongContractError(f"page {requested}: total/page controls changed")
    marker = _TOTAL.fullmatch(_clean(totals[0].get_text(" ", strip=True)))
    if marker is None or not _clean(page_inputs[0].get("value")).isdigit():
        raise AndongContractError(f"page {requested}: total/page marker changed")
    total, observed, last = (
        int(marker.group(1).replace(",", "")),
        int(marker.group(2)),
        int(marker.group(3)),
    )
    if observed != requested or int(_clean(page_inputs[0].get("value"))) != requested or total < 1 or last < 1:
        raise AndongContractError(f"page {requested}: pagination identity changed")
    registry = _filter_registry(soup)
    org_names = {name for code, name in registry[1][1:]}
    roots = soup.select("ul.search-list")
    if len(roots) != 1:
        raise AndongContractError(f"page {requested}: result root changed")
    rows: list[dict[str, Any]] = []
    empty_texts: list[str] = []
    for sequence, item in enumerate(roots[0].find_all("li", recursive=False), start=1):
        anchors = item.select("a[onclick*='fn_popup_open_totalLecture']")
        if not anchors:
            text = _clean(item.get_text(" ", strip=True))
            if text:
                empty_texts.append(text)
            continue
        if len(anchors) != 1:
            raise AndongContractError(f"page {requested}: duplicate detail controls")
        control = _CONTROL.fullmatch(_clean(anchors[0].get("onclick")))
        if control is None or _clean(anchors[0].get("href")) != "javascript:void(0);":
            raise AndongContractError(f"page {requested}: detail identity control changed")
        identity, edu_type = control.group(1), control.group(2)
        title_nodes = item.select("p.title")
        owner_nodes = title_nodes[0].select("span") if len(title_nodes) == 1 else []
        if len(title_nodes) != 1 or len(owner_nodes) != 1:
            raise AndongContractError(f"course {edu_type}:{identity}: title/owner shape changed")
        owner = _clean(owner_nodes[0].get_text(" ", strip=True))
        full_title = _clean(title_nodes[0].get_text(" ", strip=True))
        if not full_title.startswith(owner):
            raise AndongContractError(f"course {edu_type}:{identity}: owner/title boundary changed")
        title = full_title[len(owner) :].strip()
        if not title or _PHONE.search(title) or _EMAIL.search(title):
            raise AndongContractError(f"course {edu_type}:{identity}: unsafe/empty title")
        if edu_type == "6":
            if owner not in org_names and (identity, owner) not in _RETIRED_EXTERNAL_OWNER_IDENTITIES:
                raise AndongContractError(f"course {edu_type}:{identity}: external owner escaped registry")
        elif owner != _TYPE_OWNER[edu_type]:
            raise AndongContractError(f"course {edu_type}:{identity}: source owner changed")
        fields = _list_fields(item, f"{edu_type}:{identity}")
        state_nodes = item.select(".state span")
        states = {re.sub(r"\s+D[-+]\d+$", "", _clean(node.get_text(" ", strip=True))) for node in state_nodes}
        if len(states) != 1:
            raise AndongContractError(f"course {edu_type}:{identity}: source state changed")
        source_status = states.pop()
        if source_status not in _SOURCE_STATUS:
            raise AndongContractError(f"course {edu_type}:{identity}: unknown source state")
        date_values = tuple(value for label, field in fields.items() if label != "강사명" for value in _dates(field))
        if "교육기간" in fields:
            period = fields["교육기간"]
            known_reversed = _KNOWN_REVERSED_ARCHIVED_PERIODS.get((edu_type, identity, period))
            if known_reversed is not None:
                list_start, list_end = known_reversed
            else:
                list_start, list_end = _date_range(period, f"course {edu_type}:{identity} list period")
        elif date_values:
            list_start, list_end = min(date_values), max(date_values)
        else:
            list_start = list_end = None
        rows.append(
            {
                "identity": identity,
                "edu_type": edu_type,
                "list_page": requested,
                "list_sequence": sequence,
                "owner": owner,
                "title": title,
                "fields": fields,
                "source_status": source_status,
                "status": _SOURCE_STATUS[source_status],
                "list_start": list_start,
                "list_end": list_end,
            }
        )
    structural_empty = bool(empty_texts)
    if structural_empty and (rows or empty_texts != ["등록된 데이터가 없습니다."]):
        raise AndongContractError(f"page {requested}: structural empty row changed")
    if requested <= last:
        if structural_empty:
            raise AndongContractError(f"page {requested}: declared data page is empty")
        expected = ANDONG_PAGE_SIZE if requested < last else ((total - 1) % ANDONG_PAGE_SIZE + 1)
        if len(rows) != expected:
            raise AndongContractError(f"page {requested}: row count/cardinality changed")
    elif not structural_empty or rows:
        raise AndongContractError(f"page {requested}: post-last page is not structurally empty")
    identities = [(row["edu_type"], row["identity"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise AndongContractError(f"page {requested}: duplicate compound identities")
    return _Page(requested, observed, last, total, tuple(rows), registry, structural_empty)


def _page_signature(page: _Page) -> tuple[Any, ...]:
    return (
        page.total,
        page.last,
        page.registry,
        tuple(
            (
                row["identity"],
                row["edu_type"],
                row["owner"],
                row["title"],
                tuple(row["fields"].items()),
                row["source_status"],
            )
            for row in page.rows
        ),
    )


def _detail_fields(soup: BeautifulSoup, compound: str) -> dict[str, str]:
    matching: list[Any] = []
    for table in soup.select(".pop-con table.tbl.Thead"):
        labels = {_clean(node.get_text(" ", strip=True)) for node in table.select("th")}
        if "교육기간" in labels and "모집정원" in labels:
            matching.append(table)
    if len(matching) != 1:
        raise AndongContractError(f"course {compound}: main detail table changed")
    result: dict[str, str] = {}
    labels: set[str] = set()
    for tr in matching[0].select("tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            if children[index].name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                raise AndongContractError(f"course {compound}: detail field pairing changed")
            label = _clean(children[index].get_text(" ", strip=True))
            if not label or label in labels or label not in (_DETAIL_SAFE_FIELDS | _DETAIL_DISCARDED_FIELDS):
                raise AndongContractError(f"course {compound}: detail fieldset changed: {label}")
            labels.add(label)
            if label not in _DETAIL_DISCARDED_FIELDS:
                value = _clean(children[index + 1].get_text(" ", strip=True))
                if label != "교육장소" and (_PHONE.search(value) or _EMAIL.search(value)):
                    raise AndongContractError(f"course {compound}: contact-like safe field {label}")
                result[label] = value
            index += 2
    if not _DETAIL_REQUIRED_FIELDS <= labels:
        raise AndongContractError(f"course {compound}: required detail fields missing")
    if ("2차접수기간" in labels) != ("등록현황" in labels):
        raise AndongContractError(f"course {compound}: second-registration field pair changed")
    return result


def _single_hidden(soup: BeautifulSoup, identity: str, expected: str, compound: str) -> str:
    nodes = soup.select(f"input#{identity}")
    if len(nodes) != 1 or _clean(nodes[0].get("name")) != expected:
        raise AndongContractError(f"course {compound}: hidden {identity} binding changed")
    return _clean(nodes[0].get("value"))


def _safe_external_application(value: str, compound: str) -> str:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError as exc:
        raise AndongContractError(f"course {compound}: malformed external application URL") from exc
    if not (
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and not parsed.fragment
    ):
        raise AndongContractError(f"course {compound}: unsafe external application URL")
    return parsed.geturl()


def _sanitize_venue(value: str) -> str:
    result = _EMAIL.sub("", _PHONE.sub("", _clean(value))).replace("☎", " ")
    result = re.sub(r"\(\s*\)", " ", result)
    return _clean(result).strip(" ,/")


def _branch(owner: str, edu_type: str) -> tuple[str, str]:
    if edu_type == "1":
        return "안동시 평생학습관", "integrated_learning_centre_owner"
    if edu_type == "2":
        return "길거리교실", "integrated_roadclass_owner"
    if edu_type == "3":
        return "시민강사", "integrated_citizen_instructor_owner"
    if edu_type == "4":
        return "명사초청", "integrated_invited_lecture_owner"
    return owner, "integrated_external_institution_owner"


def _branch_code(branch: str, edu_type: str, org_registry: Mapping[str, str]) -> str:
    fixed = {
        "1": "ANDONG_LIFELONG_CENTER",
        "2": "ANDONG_ROAD_CLASS",
        "3": "ANDONG_CITIZEN_INSTRUCTOR",
        "4": "ANDONG_INVITED_LECTURE",
    }
    if edu_type in fixed:
        return fixed[edu_type]
    code = org_registry.get(branch, "")
    if not code:
        raise AndongContractError(f"external branch escaped institution registry: {branch}")
    return f"ANDONG_ORG_{code}"


def _capacity(value: str, compound: str) -> tuple[int, int]:
    # One inactive citizen-instructor record is incomplete upstream.  Its
    # empty capacity is accepted only for that exact public identity; it is
    # subsequently excluded because its class date has already elapsed.
    if compound == "3:1231" and not _clean(value):
        return 0, 0
    match = _CAPACITY.match(_clean(value))
    if match is None:
        raise AndongContractError(f"course {compound}: capacity changed")
    return int(match.group(1).replace(",", "")), int((match.group(2) or "0").replace(",", ""))


def _detail_contract(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
    org_registry: Mapping[str, str],
) -> dict[str, Any]:
    identity, edu_type = _clean(listed.get("identity")), _clean(listed.get("edu_type"))
    compound = f"{edu_type}:{identity}"
    fields = _detail_fields(soup, compound)
    heading_owner_nodes = soup.select("#eduTypeText")
    heading_title_nodes = soup.select("#nameText")
    state_nodes = soup.select(".pop-tit .state span")
    if len(heading_owner_nodes) != 1 or len(heading_title_nodes) != 1 or not state_nodes:
        raise AndongContractError(f"course {compound}: detail heading/state changed")
    detail_owner = _clean(heading_owner_nodes[0].get_text(" ", strip=True))
    detail_title = _clean(heading_title_nodes[0].get_text(" ", strip=True))
    states = {re.sub(r"\s+D[-+]\d+$", "", _clean(node.get_text(" ", strip=True))) for node in state_nodes}
    if len(states) != 1:
        raise AndongContractError(f"course {compound}: detail state changed")
    detail_status_text = states.pop()
    expected_owner = _clean(listed.get("owner")) if edu_type == "6" else _DETAIL_OWNER[edu_type]
    if (
        detail_owner != expected_owner
        or _normalized(detail_title) != _normalized(listed.get("title"))
        or detail_status_text not in _DETAIL_STATUS
        or _DETAIL_STATUS[detail_status_text] != _clean(listed.get("status"))
    ):
        raise AndongContractError(f"course {compound}: list/detail identity or state drift")
    if _single_hidden(soup, "selectedIdx", "idx", compound) != identity:
        raise AndongContractError(f"course {compound}: hidden course identity drift")
    if _single_hidden(soup, "selectedEduType", "selectedEduType", compound) != edu_type:
        raise AndongContractError(f"course {compound}: hidden education type drift")
    category_code = _single_hidden(soup, "selectedCategory", "selectedCategory", compound)
    selected_url = _single_hidden(soup, "selectedUrl", "selectedUrl", compound)
    if _single_hidden(soup, "searchYn", "searchYn", compound) != "N":
        raise AndongContractError(f"course {compound}: modal search binding changed")
    if edu_type == "1":
        expected_categories = [
            code
            for code in _CATEGORY_MID
            if _clean(listed.get("title")).startswith(
                {"10": "[주간교육]", "20": "[야간교육]", "30": "[특강교육]"}[code]
            )
        ]
        if expected_categories != [category_code] or selected_url:
            raise AndongContractError(f"course {compound}: learning-centre category binding changed")
    elif category_code:
        raise AndongContractError(f"course {compound}: unexpected category binding")
    if edu_type not in {"4", "6"} and selected_url:
        raise AndongContractError(f"course {compound}: unexpected external application URL")

    list_start, list_end = listed.get("list_start"), listed.get("list_end")
    period = _clean(fields["교육기간"])
    dates = _dates(period)
    current_basis = ""
    exclusion_reason = ""
    if period == "신청자 자유":
        if _clean(fields["1차접수기간"]) != "상시" or _clean(listed.get("status")) not in {"OPEN", "SCHEDULED"}:
            raise AndongContractError(f"course {compound}: on-demand period/state changed")
        start = end = None
        current_basis = "on_demand_evergreen"
    elif not period:
        start = end = None
        exclusion_reason = "missing_education_period"
    else:
        start, end = _date_range(period, f"course {compound} detail period")
        current_basis = "fixed_current_or_future" if end >= cutoff else ""
        if end < cutoff:
            exclusion_reason = "detail_period_expired"
    if list_start is not None:
        if (start, end) != (list_start, list_end):
            raise AndongContractError(f"course {compound}: list/detail education period drift")
    elif dates and start is None:
        raise AndongContractError(f"course {compound}: detail date parsing changed")

    buttons = soup.select(".btn-box a, .btn-box button")
    apply_buttons = [node for node in buttons if _clean(node.get_text(" ", strip=True)) == "신청하기"]
    close_buttons = [node for node in buttons if _clean(node.get_text(" ", strip=True)) == "닫기"]
    if len(apply_buttons) > 1 or len(close_buttons) != 1:
        raise AndongContractError(f"course {compound}: modal controls changed")
    if re.sub(r"\s+", "", _clean(close_buttons[0].get("onclick"))) != "fn_popup_close_totalLecture(this);":
        raise AndongContractError(f"course {compound}: close control changed")
    visible_application = bool(apply_buttons)
    if visible_application and re.sub(r"\s+", "", _clean(apply_buttons[0].get("onclick"))) != "moveWrite();":
        raise AndongContractError(f"course {compound}: application control changed")
    is_open = _clean(listed.get("status")) == "OPEN"
    if visible_application != is_open:
        raise AndongContractError(f"course {compound}: source status/application control drift")

    if is_open and edu_type in {"4", "6"}:
        bound_application = _safe_external_application(selected_url, compound)
        application_type = "EXTERNAL_APPLICATION_PORTAL"
    elif is_open:
        bound_application = andong_application_url(identity, edu_type, category_code)
        application_type = "ONLINE_RESERVATION_LOGIN_REQUIRED"
    else:
        bound_application = ""
        application_type = "INFO_ONLY"
    eligible_current = bool(current_basis)
    experience = "체험" in detail_title
    if experience and eligible_current:
        exclusion_reason = "experience_title"

    venue = _sanitize_venue(fields["교육장소"])
    target = _clean(fields["교육대상"])
    category = _clean(fields["교육분야"])
    if any(_PHONE.search(value) or _EMAIL.search(value) for value in (detail_title, venue, target, category)):
        raise AndongContractError(f"course {compound}: contact-like safe value survived")
    capacity_total, wait_capacity = _capacity(fields["모집정원"], compound)
    capacity_current = waitlist_current = 0
    if "등록현황" in fields:
        registration = _REGISTRATION.fullmatch(_clean(fields["등록현황"]))
        if registration is None:
            raise AndongContractError(f"course {compound}: registration counts changed")
        capacity_current = int(registration.group(1).replace(",", ""))
        waitlist_current = int(registration.group(3).replace(",", ""))
    apply_period = _clean(fields["1차접수기간"])
    apply_dates = _dates(apply_period)
    apply_start = apply_dates[0] if apply_dates else None
    apply_end = apply_dates[-1] if apply_dates else None
    if apply_period != "상시" and len(apply_dates) != 2:
        raise AndongContractError(f"course {compound}: first application period changed")
    branch, branch_basis = _branch(_clean(listed.get("owner")), edu_type)
    row: Optional[dict[str, Any]] = None
    if eligible_current:
        row = {
            "provider": ANDONG_PROVIDER,
            "provider_course_id": f"{ANDONG_PROVIDER}:{edu_type}:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": detail_title,
            "description": detail_title,
            "branch": branch,
            "branch_code": _branch_code(branch, edu_type, org_registry),
            "preserve_branch": True,
            "category": category,
            "program_type": "체험" if experience else "교육",
            "raw_url": andong_detail_url(identity, edu_type),
            "application_url": bound_application if is_open else "",
            "application_type": application_type if is_open else "INFO_ONLY",
            "application_method": _clean(fields["모집형태"]),
            "application_methods": [_clean(fields["모집형태"])],
            "reservation_available": bool(is_open and bound_application),
            "status": _clean(listed.get("status")),
            "fee": _clean(fields["수강료"]),
            "material_fee": _clean(fields["재료비(기타비용)"]),
            "period": period,
            "start_date": start.isoformat() if start is not None else "",
            "end_date": end.isoformat() if end is not None else "",
            "apply_period": apply_period,
            "apply_start": apply_start.isoformat() if apply_start is not None else "",
            "apply_end": apply_end.isoformat() if apply_end is not None else "",
            "schedule_raw": _clean(fields["교육시간"]),
            "capacity": f"{capacity_total}명",
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_current": waitlist_current,
            "waitlist_capacity": wait_capacity,
            "target": target,
            "venue": venue,
            "venue_name": branch,
            "collection_category": "공공예약",
            "domain_category": "체험·견학" if experience else "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "체험" if experience else "공공강좌",
            "service_group_policy": "locked",
            "collection_type": ANDONG_PARSER,
            "municipality_code": ANDONG_MUNICIPALITY_CODE,
            "municipality_full_name": ANDONG_MUNICIPALITY_NAME,
            "raw_fields": {
                "identity": identity,
                "edu_type": edu_type,
                "list_page": int(listed["list_page"]),
                "source_owner": _clean(listed.get("owner")),
                "source_status": _clean(listed.get("source_status")),
                "detail_status": detail_status_text,
                "source_list_fieldset": tuple(listed.get("fields", {})),
                "source_education_period": period,
                "source_schedule": _clean(fields["교육시간"]),
                "source_apply_period": apply_period,
                "source_second_apply_period": _clean(fields.get("2차접수기간")),
                "source_category": category,
                "source_target": target,
                "source_venue": venue,
                "source_recruitment": _clean(fields["모집형태"]),
                "source_category_code": category_code,
                "branch_basis": branch_basis,
                "current_basis": current_basis,
                "detail_verified": True,
                "application_control_present": visible_application,
                "education_scope_verified": not experience,
                "experience_scope_verified": experience,
                "service_family": "experience" if experience else "education",
            },
        }
    return {
        "row": row,
        "eligible_current": eligible_current,
        "experience": bool(experience and eligible_current),
        "exclusion_reason": exclusion_reason,
        "application_control_present": visible_application,
        "status": _clean(listed.get("status")),
        "branch": branch,
        "current_basis": current_basis,
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden detail/PII key persisted")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr({key: value for key, value in row.items() if key not in {"raw_url", "application_url"}})
    if _PHONE.search(payload) or _EMAIL.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail content persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now().astimezone().date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("today must be an ISO date")


def andong_library_list_url(menu_idx: str, large_category_idx: str) -> str:
    expected = {("362", "16"), ("368", "23")}
    if (str(menu_idx), str(large_category_idx)) not in expected:
        raise ValueError("invalid Andong library ledger identity")
    return f"https://{ANDONG_LIBRARY_HOST}{ANDONG_LIBRARY_LIST_PATH}?" + urlencode(
        (("menu_idx", str(menu_idx)), ("searchCate1", str(large_category_idx)))
    )


def andong_library_detail_url(
    menu_idx: str,
    large_category_idx: str,
    group_idx: str,
    category_idx: str,
    teach_idx: str,
) -> str:
    if (str(menu_idx), str(large_category_idx)) not in {("362", "16"), ("368", "23")}:
        raise ValueError("invalid Andong library ledger identity")
    if any(_IDENTITY.fullmatch(str(value)) is None for value in (group_idx, teach_idx)):
        raise ValueError("invalid Andong library course identity")
    if not str(category_idx).isdigit():
        raise ValueError("invalid Andong library category identity")
    return f"https://{ANDONG_LIBRARY_HOST}{ANDONG_LIBRARY_DETAIL_PATH}?" + urlencode(
        (
            ("menu_idx", str(menu_idx)),
            ("group_idx", str(group_idx)),
            ("category_idx", str(category_idx)),
            ("teach_idx", str(teach_idx)),
            ("searchCate1", str(large_category_idx)),
            ("large_category_idx", "0"),
        )
    )


def andong_library_application_url(
    menu_idx: str,
    large_category_idx: str,
    group_idx: str,
    category_idx: str,
    teach_idx: str,
    *,
    apply_status: str = "1",
) -> str:
    # This URL is exposed only as a navigation target and is never requested.
    andong_library_detail_url(menu_idx, large_category_idx, group_idx, category_idx, teach_idx)
    if apply_status not in {"1", "2"}:
        raise ValueError("invalid Andong library application status")
    return f"https://{ANDONG_LIBRARY_HOST}{ANDONG_LIBRARY_APPLICATION_PATH}?" + urlencode(
        (
            ("editMode", "ADD"),
            ("homepage_id", "h12"),
            ("group_idx", str(group_idx)),
            ("category_idx", str(category_idx)),
            ("teach_idx", str(teach_idx)),
            ("large_category_idx", str(large_category_idx)),
            ("apply_status", apply_status),
            ("menu_idx", str(menu_idx)),
        )
    )


def _allowed_library_request_url(url: str) -> bool:
    parsed = urlparse(url)
    if not (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == ANDONG_LIBRARY_HOST
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.fragment
    ):
        return False
    try:
        pairs = _query(url)
    except ValueError:
        return False
    if parsed.path == ANDONG_LIBRARY_LIST_PATH:
        return pairs in (
            [("menu_idx", "362"), ("searchCate1", "16")],
            [("menu_idx", "368"), ("searchCate1", "23")],
        )
    if parsed.path != ANDONG_LIBRARY_DETAIL_PATH or len(pairs) != 6:
        return False
    values = dict(pairs)
    return bool(
        pairs[0][0] == "menu_idx"
        and pairs[1][0] == "group_idx"
        and pairs[2][0] == "category_idx"
        and pairs[3][0] == "teach_idx"
        and pairs[4][0] == "searchCate1"
        and pairs[5] == ("large_category_idx", "0")
        and (values["menu_idx"], values["searchCate1"]) in {("362", "16"), ("368", "23")}
        and _IDENTITY.fullmatch(values["group_idx"])
        and values["category_idx"].isdigit()
        and _IDENTITY.fullmatch(values["teach_idx"])
    )


def _request_library_soup(
    url: str,
    timeout: int,
    session_factory: SessionFactory,
    fetcher: Fetcher,
) -> BeautifulSoup:
    if not _allowed_library_request_url(url):
        raise AndongContractError("request left the audited Andong library list/detail contract")
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        session = session_factory()
        try:
            response = fetcher(session, url, timeout)
            status = int(getattr(response, "status_code", 200))
            if status != 200:
                raise AndongContractError(f"unexpected HTTP status {status}")
            headers = getattr(response, "headers", {}) or {}
            if headers.get("Location"):
                raise AndongContractError("redirect response is not accepted")
            final_url = _clean(getattr(response, "url", url)) or url
            if not _allowed_library_request_url(final_url) or _query(final_url) != _query(url):
                raise AndongContractError("response URL changed")
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", response)).encode("utf-8")
            if not content or len(content) > ANDONG_MAX_HTML_BYTES:
                raise AndongContractError("empty or oversized official HTML response")
            return BeautifulSoup(content, "lxml", from_encoding="utf-8")
        except (requests.RequestException, TimeoutError) as exc:
            last_error = exc
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()
    assert last_error is not None
    raise last_error


_LIBRARY_BRANCHES = {
    "0000": ("통합", "안동시립도서관"),
    "0003": ("중앙", "안동시립중앙도서관"),
    "0001": ("웅부", "안동시립웅부도서관"),
    "0002": ("어린이", "안동시립어린이도서관"),
}
_LIBRARY_LIST_STATUS = {
    "접수하기": "OPEN",
    "대기자신청": "WAITING",
    "신청대기": "SCHEDULED",
    "정원마감": "CLOSED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
    "행사종료": "CLOSED",
}


def _library_control_identity(node: Any, listed: Mapping[str, Any], *, detail: bool) -> None:
    expected_apply_status = (
        "2" if _clean(listed.get("source_status")) == "대기자신청" else "1"
    )
    expected = {
        "keyvalue1": "h12",
        "keyvalue2": _clean(listed.get("group_idx")),
        "keyvalue3": _clean(listed.get("category_idx")),
        "keyvalue4": _clean(listed.get("teach_idx")),
        "apply_status": expected_apply_status,
    }
    if any(_clean(node.get(key)) != value for key, value in expected.items()):
        raise AndongContractError(f"library course {listed.get('teach_idx')}: application identity drift")
    if detail:
        key5 = _clean(node.get("keyvalue5"))
        if key5 not in {"0", _clean(listed.get("large_category_idx"))}:
            raise AndongContractError(f"library course {listed.get('teach_idx')}: application category drift")
    elif _clean(node.get("keyvalue5")) != _clean(listed.get("large_category_idx")):
        raise AndongContractError(f"library course {listed.get('teach_idx')}: list application category drift")


def _parse_library_ledger(
    soup: BeautifulSoup,
    menu_idx: str,
    large_category_idx: str,
) -> _LibraryLedger:
    forms = soup.select("form#teach")
    if (
        len(forms) != 1
        or _clean(forms[0].get("action")) != "/andonglibrary/module/teach/student/save.do"
        or _clean(forms[0].get("method")).upper() != "POST"
    ):
        raise AndongContractError("library applicant form boundary changed")
    expected_hiddens = {
        "group_idx": ["0"],
        "teach_idx": ["0"],
        "menu_idx": [menu_idx],
        "category_idx": ["0", "0"],
        "searchCate1": [large_category_idx],
        "large_category_idx": ["0"],
    }
    for name, expected in expected_hiddens.items():
        values = [_clean(node.get("value")) for node in forms[0].select(f"input[name='{name}']")]
        if values != expected:
            raise AndongContractError(f"library {name} ledger binding changed")
    orgs = _option_registry(soup, "org_code")
    expected_orgs = (("", "전체"),) + tuple((code, names[0]) for code, names in _LIBRARY_BRANCHES.items())
    if orgs != expected_orgs:
        raise AndongContractError("library branch registry changed")
    if soup.select(".paging, .pagination, .paginate"):
        raise AndongContractError("library ledger unexpectedly became paginated")
    tables = soup.select("table.list01")
    if len(tables) != 1:
        raise AndongContractError("library result table changed")
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in tables[0].select("thead th"))
    if headers != (
        "도서관명 · 분류",
        "분류",
        "제목",
        "모집 방법 · 정원",
        "정원",
        "행사기간",
        "접수 상태",
    ):
        raise AndongContractError("library result columns changed")
    rows: list[dict[str, Any]] = []
    for sequence, tr in enumerate(tables[0].select("tbody > tr"), start=1):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 7:
            raise AndongContractError("library result row shape changed")
        branch_nodes = cells[0].select("span.codeName")
        if len(branch_nodes) != 1:
            raise AndongContractError("library branch cell changed")
        branch_classes = [value for value in branch_nodes[0].get("class", []) if value.startswith("phlib")]
        if len(branch_classes) != 1:
            raise AndongContractError("library branch code changed")
        branch_code = branch_classes[0][5:]
        branch_short = _clean(branch_nodes[0].get_text(" ", strip=True))
        if branch_code not in _LIBRARY_BRANCHES or _LIBRARY_BRANCHES[branch_code][0] != branch_short:
            raise AndongContractError("library row escaped branch registry")
        category = _clean(cells[1].get_text(" ", strip=True))
        mobile_categories = [_clean(node.get_text(" ", strip=True)) for node in cells[0].select("span.moBr")]
        if not category or mobile_categories != [category]:
            raise AndongContractError("library row category boundary changed")
        controls = cells[2].select("a.detail-btn")
        if len(controls) != 1 or _clean(controls[0].get("href")) != "#":
            raise AndongContractError("library detail control changed")
        group_idx = _clean(controls[0].get("keyvalue1"))
        category_idx = _clean(controls[0].get("keyvalue2"))
        teach_idx = _clean(controls[0].get("keyvalue3"))
        if (
            _IDENTITY.fullmatch(group_idx) is None
            or not category_idx.isdigit()
            or _IDENTITY.fullmatch(teach_idx) is None
        ):
            raise AndongContractError("library detail identity changed")
        title = _clean(controls[0].get_text(" ", strip=True))
        if not title or _PHONE.search(title) or _EMAIL.search(title):
            raise AndongContractError(f"library course {teach_idx}: unsafe/empty title")
        descriptions = [_clean(node.get_text(" ", strip=True)) for node in cells[2].select("dd.con")]
        if (
            len(descriptions) != 2
            or not descriptions[0].startswith("대상 : ")
            or not descriptions[1].startswith("장소 : ")
        ):
            raise AndongContractError(f"library course {teach_idx}: target/venue list shape changed")
        counts = _LIBRARY_COUNTS.fullmatch(_clean(cells[4].get_text(" ", strip=True)))
        if counts is None or not _clean(cells[3].get_text(" ", strip=True)).startswith("인터넷접수"):
            raise AndongContractError(f"library course {teach_idx}: recruitment counts changed")
        capacity_current, capacity_total, wait_current, wait_total = (
            int(value.replace(",", "")) for value in counts.groups()
        )
        target_text = descriptions[0][len("대상 : ") :].strip()
        target_match = re.fullmatch(r"(.+?)\s+(\d[\d,]*)명", target_text)
        if target_match is None or int(target_match.group(2).replace(",", "")) != capacity_total:
            raise AndongContractError(f"library course {teach_idx}: target/capacity boundary changed")
        target_value = _clean(target_match.group(1))
        venue = _sanitize_venue(descriptions[1][len("장소 : ") :])
        date_text = _clean(cells[5].get_text(" ", strip=True))
        start, end = _date_range(date_text, f"library course {teach_idx} list period")
        times = _TIME.findall(date_text)
        if len(times) != 2:
            raise AndongContractError(f"library course {teach_idx}: list schedule changed")
        schedule = f"{times[0][0].zfill(2)}:{times[0][1]} ~ {times[1][0].zfill(2)}:{times[1][1]}"
        user_checks = [node for node in cells[6].select("a") if _clean(node.get("onclick")) == "userCheckQr();"]
        status_controls = [node for node in cells[6].select("a") if node not in user_checks]
        if len(user_checks) != 1 or _clean(user_checks[0].get("href")) != "#usercheck" or len(status_controls) != 1:
            raise AndongContractError(f"library course {teach_idx}: status controls changed")
        source_status = _clean(status_controls[0].get_text(" ", strip=True))
        if source_status not in _LIBRARY_LIST_STATUS:
            raise AndongContractError(f"library course {teach_idx}: unknown source status")
        listed = {
            "menu_idx": menu_idx,
            "large_category_idx": large_category_idx,
            "group_idx": group_idx,
            "category_idx": category_idx,
            "teach_idx": teach_idx,
            "source_status": source_status,
        }
        actionable = _LIBRARY_LIST_STATUS[source_status] in {"OPEN", "WAITING"}
        if actionable:
            if "add" not in status_controls[0].get("class", []) or _clean(status_controls[0].get("href")):
                raise AndongContractError(f"library course {teach_idx}: open application control changed")
            _library_control_identity(status_controls[0], listed, detail=False)
            expected_apply_status = "2" if source_status == "대기자신청" else "1"
            if (
                _clean(status_controls[0].get("keyvalue4")) != teach_idx
                or _clean(status_controls[0].get("keyvalue5")) != large_category_idx
                or _clean(status_controls[0].get("apply_status")) != expected_apply_status
            ):
                raise AndongContractError(f"library course {teach_idx}: list application identity changed")
        elif _clean(status_controls[0].get("href")) != "javascript:void(0);":
            raise AndongContractError(f"library course {teach_idx}: inactive control changed")
        rows.append(
            {
                **listed,
                "list_sequence": sequence,
                "branch_code": branch_code,
                "branch_short": branch_short,
                "category": category,
                "title": title,
                "target": target_value,
                "venue": venue,
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "waitlist_current": wait_current,
                "waitlist_capacity": wait_total,
                "start": start,
                "end": end,
                "schedule": schedule,
                "status": _LIBRARY_LIST_STATUS[source_status],
                "list_application_control": actionable,
            }
        )
    identities = [(row["large_category_idx"], row["group_idx"], row["category_idx"], row["teach_idx"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise AndongContractError("library ledger duplicated compound identities")
    return _LibraryLedger(menu_idx, large_category_idx, tuple(rows), orgs, not rows)


def _library_ledger_signature(ledger: _LibraryLedger) -> tuple[Any, ...]:
    return (
        ledger.menu_idx,
        ledger.large_category_idx,
        ledger.branch_registry,
        tuple(tuple(sorted(row.items())) for row in ledger.rows),
        ledger.structural_empty,
    )


_LIBRARY_DETAIL_SAFE_FIELDS = {
    "기관",
    "행사 분류",
    "준비물 및 재료비",
    "참가비",
    "행사기간(*)",
    "행사기간",
    "행사시간",
    "행사장소",
    "행사대상",
    "접수기간",
    "현재 참여 / 모집",
}
_LIBRARY_DETAIL_DISCARDED_FIELDS = {
    "행사 설명",
    "강사명",
    "행사요일",
    "학년제한",
    "행사내용",
    "담당부서 및 전화번호",
    "현재 모집 상세현황",
}
_LIBRARY_DETAIL_REQUIRED_FIELDS = {
    "기관",
    "행사 분류",
    "준비물 및 재료비",
    "참가비",
    "행사기간(*)",
    "행사기간",
    "행사시간",
    "행사장소",
    "행사대상",
    "접수기간",
    "현재 참여 / 모집",
}
_LIBRARY_PERFORMANCE_IDENTITIES = {
    ("23", "144", "0", "833", "모래가 들려주는 이야기"),
}
_LIBRARY_EVENT_EDUCATION_IDENTITIES = {
    ("23", "147", "0", "834", "8월 썬킴 작가초대석"),
}


def _library_detail_fields(
    soup: BeautifulSoup, teach_idx: str, expected_title: str
) -> dict[str, str]:
    tables = soup.select("table.tstyle.nohead")
    if len(tables) != 1:
        raise AndongContractError(f"library course {teach_idx}: detail table changed")
    values: dict[str, set[str]] = {}
    labels: set[str] = set()
    allowed = _LIBRARY_DETAIL_SAFE_FIELDS | _LIBRARY_DETAIL_DISCARDED_FIELDS
    for tr in tables[0].select("tr"):
        children = tr.find_all(["th", "td"], recursive=False)
        if not children:
            aggregate = _clean(tr.get_text(" ", strip=True))
            if aggregate and re.fullmatch(
                r",?\s*현재 대기자\s*/\s*대기자:\s*\d[\d,]*명\s*/\s*\d[\d,]*\s*명",
                aggregate,
            ) is None:
                raise AndongContractError(
                    f"library course {teach_idx}: unclassified detail row"
                )
            continue
        if (
            len(children) == 1
            and children[0].name == "th"
            and _clean(children[0].get_text(" ", strip=True)) == "현재 모집 상세현황"
        ):
            labels.add("현재 모집 상세현황")
            continue
        if len(children) == 1 and children[0].name == "th":
            images = children[0].select(":scope > img")
            if (
                len(images) == 1
                and _normalized(images[0].get("alt")) == _normalized(expected_title)
                and re.fullmatch(
                    r"/data/teach/h12/img/[A-Za-z0-9_.-]+\.(?:gif|jpe?g|png|webp)",
                    _clean(images[0].get("src")),
                    re.IGNORECASE,
                )
                is not None
                and not _clean(children[0].get_text(" ", strip=True))
            ):
                continue
        index = 0
        while index < len(children):
            if children[index].name != "th" or index + 1 >= len(children) or children[index + 1].name != "td":
                raise AndongContractError(f"library course {teach_idx}: detail field pairing changed")
            label = _clean(children[index].get_text(" ", strip=True))
            if label not in allowed:
                raise AndongContractError(f"library course {teach_idx}: detail fieldset changed: {label}")
            labels.add(label)
            if label in _LIBRARY_DETAIL_SAFE_FIELDS:
                value = _clean(children[index + 1].get_text(" ", strip=True))
                if value:
                    values.setdefault(label, set()).add(value)
            # Discarded cells, especially instructor/contact/content, are never
            # converted to Python strings.
            index += 2
    if not _LIBRARY_DETAIL_REQUIRED_FIELDS <= labels:
        raise AndongContractError(f"library course {teach_idx}: required detail fields missing")
    result: dict[str, str] = {}
    for label in _LIBRARY_DETAIL_SAFE_FIELDS:
        options = values.get(label, set())
        if len(options) > 1:
            raise AndongContractError(f"library course {teach_idx}: conflicting detail field {label}")
        result[label] = next(iter(options), "")
    return result


def _library_scope_reason(listed: Mapping[str, Any], detail_title: str) -> str:
    if "체험" in detail_title:
        return "experience_title"
    if "문자 안내 희망" in detail_title:
        return "notification_subscription"
    identity = (
        _clean(listed.get("large_category_idx")),
        _clean(listed.get("group_idx")),
        _clean(listed.get("category_idx")),
        _clean(listed.get("teach_idx")),
        detail_title,
    )
    if identity in _LIBRARY_PERFORMANCE_IDENTITIES:
        return "performance"
    if identity in _LIBRARY_EVENT_EDUCATION_IDENTITIES:
        return ""
    if _clean(listed.get("large_category_idx")) == "16":
        return ""
    category = _clean(listed.get("category"))
    if category in {"독서교실", "문화가 있는 날", "문화교실", "교육", "강좌"}:
        return ""
    if re.fullmatch(
        r"(?:20\d{2}년?\s*)?(?:봄|여름|가을|겨울)?\s*독서교실",
        category,
    ):
        return ""
    raise AndongContractError(
        f"library course {listed.get('teach_idx')}: unclassified event-category education boundary"
    )


def _library_detail_contract(listed: Mapping[str, Any], soup: BeautifulSoup) -> dict[str, Any]:
    teach_idx = _clean(listed.get("teach_idx"))
    headings = soup.select(".teach_top > h3")
    if len(headings) != 1:
        raise AndongContractError(f"library course {teach_idx}: detail heading changed")
    detail_title = _clean(headings[0].get_text(" ", strip=True))
    list_title = _clean(listed.get("title"))
    title_matches = _normalized(detail_title) == _normalized(list_title) or (
        list_title.endswith("...") and _normalized(detail_title).startswith(_normalized(list_title[:-3]))
    )
    if not detail_title or not title_matches or _PHONE.search(detail_title) or _EMAIL.search(detail_title):
        raise AndongContractError(f"library course {teach_idx}: list/detail title drift")
    fields = _library_detail_fields(soup, teach_idx, detail_title)
    if fields["기관"] != _clean(listed.get("branch_short")) or fields["행사 분류"] != _clean(listed.get("category")):
        raise AndongContractError(f"library course {teach_idx}: list/detail branch or category drift")
    start, end = _date_range(fields["행사기간"], f"library course {teach_idx} detail period")
    starred_start, starred_end = _date_range(fields["행사기간(*)"], f"library course {teach_idx} starred detail period")
    if (
        (start, end) != (starred_start, starred_end)
        or (start, end) != (listed.get("start"), listed.get("end"))
        or _normalized(fields["행사시간"]) != _normalized(listed.get("schedule"))
    ):
        raise AndongContractError(f"library course {teach_idx}: list/detail schedule drift")
    counts = _LIBRARY_DETAIL_COUNTS.fullmatch(fields["현재 참여 / 모집"])
    if counts is None:
        raise AndongContractError(f"library course {teach_idx}: detail capacity changed")
    current_count, total_count = (int(value.replace(",", "")) for value in counts.groups())
    venue = _sanitize_venue(fields["행사장소"])
    target = _clean(fields["행사대상"])
    if (
        current_count != int(listed.get("capacity_current", -1))
        or total_count != int(listed.get("capacity_total", -1))
        or _normalized(venue) != _normalized(listed.get("venue"))
        or _normalized(target) != _normalized(listed.get("target"))
    ):
        raise AndongContractError(f"library course {teach_idx}: list/detail capacity, target, or venue drift")
    apply_dates = _dates(fields["접수기간"])
    if len(apply_dates) != 2:
        raise AndongContractError(f"library course {teach_idx}: application period changed")
    status_buttons = [node for node in soup.select("a.btn") if _clean(node.get("id")) != "back-btn"]
    if len(status_buttons) != 1:
        raise AndongContractError(f"library course {teach_idx}: detail status controls changed")
    detail_status = _clean(status_buttons[0].get_text(" ", strip=True))
    expected_detail_statuses = {
        "접수하기": {"수강신청", "접수하기"},
        "대기자신청": {"대기자신청"},
        "신청대기": {"신청대기"},
        "정원마감": {"정원마감"},
        "접수마감": {"접수마감"},
        "신청마감": {"신청마감"},
        "행사종료": {"행사종료"},
    }
    if detail_status not in expected_detail_statuses[_clean(listed.get("source_status"))]:
        raise AndongContractError(f"library course {teach_idx}: list/detail status drift")
    status = _clean(listed.get("status"))
    is_actionable = status in {"OPEN", "WAITING"}
    if is_actionable:
        if "add" not in status_buttons[0].get("class", []) or _clean(status_buttons[0].get("href")):
            raise AndongContractError(f"library course {teach_idx}: detail application control changed")
        _library_control_identity(status_buttons[0], listed, detail=True)
    elif _clean(status_buttons[0].get("href")) != "javascript:void(0);":
        raise AndongContractError(f"library course {teach_idx}: inactive detail control changed")
    scope_reason = _library_scope_reason(listed, detail_title)
    application_url = andong_library_application_url(
        _clean(listed.get("menu_idx")),
        _clean(listed.get("large_category_idx")),
        _clean(listed.get("group_idx")),
        _clean(listed.get("category_idx")),
        teach_idx,
        apply_status=(
            "2" if _clean(listed.get("source_status")) == "대기자신청" else "1"
        ),
    )
    branch_code = _clean(listed.get("branch_code"))
    branch = _LIBRARY_BRANCHES[branch_code][1]
    is_experience = scope_reason == "experience_title"
    row: Optional[dict[str, Any]] = None
    if not scope_reason or is_experience:
        row = {
            "provider": (
                ANDONG_LIBRARY_CULTURE_PROVIDER
                if _clean(listed.get("large_category_idx")) == "16"
                else ANDONG_LIBRARY_EVENT_PROVIDER
            ),
            "provider_course_id": (
                f"{ANDONG_LIBRARY_CULTURE_PROVIDER if _clean(listed.get('large_category_idx')) == '16' else ANDONG_LIBRARY_EVENT_PROVIDER}:"
                f"{listed.get('large_category_idx')}:{listed.get('group_idx')}:{listed.get('category_idx')}:{teach_idx}"
            ),
            "prefer_incoming_provider_course_id": True,
            "title": detail_title,
            "description": detail_title,
            "branch": branch,
            "branch_code": f"ANDONG_LIBRARY_{branch_code}",
            "preserve_branch": True,
            "category": fields["행사 분류"],
            "program_type": "체험" if is_experience else "교육",
            "raw_url": andong_library_detail_url(
                _clean(listed.get("menu_idx")),
                _clean(listed.get("large_category_idx")),
                _clean(listed.get("group_idx")),
                _clean(listed.get("category_idx")),
                teach_idx,
            ),
            "application_url": application_url if is_actionable else "",
            "application_type": (
                "ONLINE_WAITLIST_LOGIN_REQUIRED"
                if status == "WAITING"
                else "ONLINE_RESERVATION_LOGIN_REQUIRED"
                if status == "OPEN"
                else "INFO_ONLY"
            ),
            "application_method": "인터넷접수",
            "application_methods": ["인터넷접수"],
            "reservation_available": bool(is_actionable),
            "status": status,
            "fee": fields["참가비"],
            "material_fee": fields["준비물 및 재료비"],
            "period": fields["행사기간"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": fields["접수기간"],
            "apply_start": apply_dates[0].isoformat(),
            "apply_end": apply_dates[1].isoformat(),
            "schedule_raw": fields["행사시간"],
            "capacity": f"{total_count}명",
            "capacity_current": current_count,
            "capacity_total": total_count,
            "waitlist_current": int(listed.get("waitlist_current", 0)),
            "waitlist_capacity": int(listed.get("waitlist_capacity", 0)),
            "target": target,
            "venue": venue,
            "venue_name": venue,
            "collection_category": "공공예약",
            "domain_category": "체험·견학" if is_experience else "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "체험" if is_experience else "공공강좌",
            "service_group_policy": "locked",
            "collection_type": ANDONG_LIBRARY_PARSER,
            "municipality_code": ANDONG_MUNICIPALITY_CODE,
            "municipality_full_name": ANDONG_MUNICIPALITY_NAME,
            "raw_fields": {
                "ledger_menu_idx": _clean(listed.get("menu_idx")),
                "large_category_idx": _clean(listed.get("large_category_idx")),
                "group_idx": _clean(listed.get("group_idx")),
                "category_idx": _clean(listed.get("category_idx")),
                "teach_idx": teach_idx,
                "list_sequence": int(listed.get("list_sequence", 0)),
                "source_branch": _clean(listed.get("branch_short")),
                "source_category": fields["행사 분류"],
                "source_status": _clean(listed.get("source_status")),
                "detail_status": detail_status,
                "source_period": fields["행사기간"],
                "source_schedule": fields["행사시간"],
                "source_apply_period": fields["접수기간"],
                "source_target": target,
                "source_venue": venue,
                "source_recruitment_method": "인터넷접수",
                "education_scope_basis": "official_experience_title"
                if is_experience
                else "library_culture_ledger"
                if _clean(listed.get("large_category_idx")) == "16"
                else "audited_event_education_category",
                "detail_verified": True,
                "application_control_present": is_actionable,
                "education_scope_verified": not is_experience,
                "experience_scope_verified": is_experience,
                "service_family": "experience" if is_experience else "education",
            },
        }
    return {
        "row": row,
        "eligible_current": True,
        "experience": is_experience,
        "exclusion_reason": "" if is_experience else scope_reason,
        "application_control_present": is_actionable,
        "status": _clean(listed.get("status")),
        "branch": branch,
    }


def _library_base_meta(provider: str, candidate_id: str, canonical_url: str) -> dict[str, Any]:
    meta = _base_meta()
    meta.update(
        {
            "owner_provider": provider,
            "canonical_candidate_id": candidate_id,
            "canonical_url": canonical_url,
            "boundary_mode": "single unpaginated current-public ledger plus exact full-ledger recheck",
            "identity_namespace": "library large_category_idx/group_idx/category_idx/teach_idx",
            "integrated_identity_overlap_count": 0,
        }
    )
    return meta


def _collect_andong_library_education(
    target: Any,
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    max_workers: int,
    today: Optional[date | datetime | str],
    session_factory: Optional[SessionFactory],
    fetcher: Optional[Fetcher],
    dedupe_rows: Optional[DedupeRows],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    provider = _clean(_value(target, "provider"))
    if provider == ANDONG_LIBRARY_CULTURE_PROVIDER:
        menu_idx, large_category_idx = "362", "16"
        candidate_id, canonical_url = ANDONG_LIBRARY_CULTURE_CANDIDATE_ID, ANDONG_LIBRARY_CULTURE_URL
    else:
        menu_idx, large_category_idx = "368", "23"
        candidate_id, canonical_url = ANDONG_LIBRARY_EVENT_CANDIDATE_ID, ANDONG_LIBRARY_EVENT_URL
    meta = _library_base_meta(provider, candidate_id, canonical_url)
    if not _is_andong_library_target(target):
        meta["configured_collection_error"] = "target does not match a canonical Andong library teach ledger"
        return [], ANDONG_LIBRARY_PARSER, meta
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < minimum
            for value, minimum in ((timeout, 1), (max_pages, 1), (detail_limit, 0), (max_workers, 1))
        )
        or max_workers > 32
    ):
        meta.update({"source_cap_reached": True, "configured_collection_error": "invalid collection limits"})
        return [], ANDONG_LIBRARY_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ANDONG_LIBRARY_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    workers = min(max_workers, ANDONG_MAX_WORKERS)
    errors: list[str] = []
    result: list[dict[str, Any]] = []

    def fetch_ledger() -> _LibraryLedger:
        url = andong_library_list_url(menu_idx, large_category_idx)
        return _parse_library_ledger(
            _request_library_soup(url, timeout, factory, current_fetcher),
            menu_idx,
            large_category_idx,
        )

    try:
        first = fetch_ledger()
        recheck = fetch_ledger()
        meta.update(
            {
                "pages": 2,
                "list_requests": 2,
                "required_list_requests": 2,
                "data_pages": 1,
                "declared_last_page": 1,
                "boundary_rechecks": 1,
            }
        )
        if _library_ledger_signature(first) != _library_ledger_signature(recheck):
            raise AndongContractError("library full-ledger boundary recheck changed")
        listed = list(first.rows)
        identities = [
            (row["large_category_idx"], row["group_idx"], row["category_idx"], row["teach_idx"]) for row in listed
        ]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            raise AndongContractError("library ledger duplicated compound identities")
        candidates = [row for row in listed if row["end"] >= cutoff]
        meta.update(
            {
                "source_total": len(listed),
                "source_rows": len(listed),
                "identity_duplicate_count": duplicate_count,
                "source_owner_counts": dict(sorted(Counter(row["branch_short"] for row in listed).items())),
                "source_status_counts": dict(Counter(row["source_status"] for row in listed)),
                "source_experience_count": sum("체험" in row["title"] for row in listed),
                "fixed_date_current_count": len(candidates),
                "current_candidate_count": len(candidates),
                "archived_rows_skipped_before_detail": len(listed) - len(candidates),
                "pagination_complete": True,
                "institution_registry": dict(first.branch_registry),
            }
        )
        if len(candidates) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": f"detail_limit cap allows {detail_limit} of {len(candidates)} library details",
                }
            )
            return [], ANDONG_LIBRARY_PARSER, meta
        meta["detail_attempts"] = len(candidates)

        def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
            try:
                url = andong_library_detail_url(
                    _clean(listed_row.get("menu_idx")),
                    _clean(listed_row.get("large_category_idx")),
                    _clean(listed_row.get("group_idx")),
                    _clean(listed_row.get("category_idx")),
                    _clean(listed_row.get("teach_idx")),
                )
                soup = _request_library_soup(url, timeout, factory, current_fetcher)
                return _library_detail_contract(listed_row, soup), ""
            except Exception as exc:
                return None, f"detail library:{listed_row.get('teach_idx')}: {type(exc).__name__}: {_clean(exc)}"

        contracts: list[dict[str, Any]] = []
        if candidates:
            with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as pool:
                detail_results = list(pool.map(fetch_detail, candidates))
            for contract, error in detail_results:
                if error:
                    errors.append(error)
                    meta["detail_errors"] += 1
                elif contract is not None:
                    contracts.append(contract)
                    meta["detail_pages"] += 1
                    meta["pages"] += 1
        details_complete = not errors and len(contracts) == len(candidates) == meta["detail_pages"]
        controls_complete = details_complete and all(
            contract["status"] not in {"OPEN", "WAITING"}
            or contract["application_control_present"]
            for contract in contracts
        )
        detailed_rows = [contract["row"] for contract in contracts if contract.get("row") is not None]
        if details_complete and controls_complete:
            for row in detailed_rows:
                errors.extend(_privacy_errors(row))
            if not errors:
                try:
                    result = list((dedupe_rows or _dedupe_default)(detailed_rows))
                except Exception as exc:
                    errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                if len(result) != len(detailed_rows):
                    errors.append(f"dedupe changed official identity cardinality {len(detailed_rows)} to {len(result)}")
                    result = []
        snapshot_complete = details_complete and controls_complete and not errors
        if not snapshot_complete:
            result = []
        exclusion_counts = Counter(
            contract["exclusion_reason"] for contract in contracts if contract["exclusion_reason"]
        )
        meta.update(
            {
                "current_source_count": len(contracts),
                "experience_count": sum(contract["experience"] for contract in contracts),
                "experience_excluded_count": 0,
                "education_scope_excluded_count": sum(exclusion_counts.values()),
                "detail_exclusion_counts": dict(exclusion_counts),
                "branch_counts": dict(sorted(Counter(row["branch"] for row in result).items())),
                "status_counts": dict(Counter(row["status"] for row in result)),
                "application_control_count": sum(row["reservation_available"] for row in result),
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not result),
                "no_current_reason": "no current/future education or experience in this library ledger"
                if snapshot_complete and not result
                else "",
                "municipality_coverage": [ANDONG_MUNICIPALITY_CODE],
                "discovery_audit": dict(ANDONG_DISCOVERY_AUDIT),
                "owner_boundary_audit": {key: dict(value) for key, value in ANDONG_OWNER_BOUNDARY_AUDIT.items()},
                "pii_fields_never_persisted": list(ANDONG_PII_FIELDS_NEVER_PERSISTED),
                "pii_payload_persisted": False,
                "forbidden_applicant_endpoint_requests": 0,
                "separate_from_integrated_owner": True,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return result, ANDONG_LIBRARY_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], ANDONG_LIBRARY_PARSER, meta


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "declared_last_page": 0,
        "post_last_empty_page": 0,
        "boundary_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "fixed_date_current_count": 0,
        "status_detail_candidate_count": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "evergreen_current_count": 0,
        "experience_excluded_count": 0,
        "source_experience_count": 0,
        "detail_inactive_or_invalid_count": 0,
        "archived_rows_skipped_before_detail": 0,
        "identity_duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": ANDONG_MUNICIPALITY_CODE,
        "municipality_name": ANDONG_MUNICIPALITY_NAME,
        "owner_provider": ANDONG_PROVIDER,
        "canonical_candidate_id": ANDONG_CANONICAL_CANDIDATE_ID,
        "canonical_url": ANDONG_CANONICAL_URL,
        "rejected_filter_provider": ANDONG_REVIEW_FILTER_PROVIDER,
        "rejected_filter_candidate_id": ANDONG_REVIEW_FILTER_CANDIDATE_ID,
        "rejected_filter_url": ANDONG_REVIEW_FILTER_URL,
        "boundary_mode": "all 24-row pages plus exact post-last empty sentinel and stable first/last rechecks",
    }


def _collect_andong_integrated_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 300,
    detail_limit: int = 500,
    max_workers: int = ANDONG_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic current/future Andong integrated-education snapshot."""

    meta = _base_meta()
    if not _is_andong_integrated_target(target):
        meta["configured_collection_error"] = "target does not match canonical Andong integrated education owner"
        return [], ANDONG_PARSER, meta
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < minimum
            for value, minimum in ((timeout, 1), (max_pages, 1), (detail_limit, 0), (max_workers, 1))
        )
        or max_workers > 32
    ):
        meta.update({"source_cap_reached": True, "configured_collection_error": "invalid collection limits"})
        return [], ANDONG_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], ANDONG_PARSER, meta
    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    workers = min(max_workers, ANDONG_MAX_WORKERS)
    errors: list[str] = []
    result: list[dict[str, Any]] = []

    def fetch_page(number: int) -> _Page:
        return _parse_page(_request_soup(andong_list_url(number), timeout, factory, current_fetcher), number)

    try:
        first = fetch_page(1)
        meta["list_requests"] = meta["pages"] = 1
        last, total = first.last, first.total
        meta["declared_last_page"] = last
        if last > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": f"max_pages cap allows {max_pages} of {last} declared pages",
                }
            )
            return [], ANDONG_PARSER, meta
        pages: dict[int, _Page] = {1: first}
        if last > 1:
            with ThreadPoolExecutor(max_workers=min(workers, last - 1)) as pool:
                fetched = list(pool.map(fetch_page, range(2, last + 1)))
            pages.update({page.requested: page for page in fetched})
            meta["list_requests"] += len(fetched)
            meta["pages"] += len(fetched)
        for page in pages.values():
            if page.last != last or page.total != total:
                raise AndongContractError("declared total/page boundary changed during traversal")
            if page.registry != first.registry:
                raise AndongContractError("institution/filter registry changed during traversal")
        sentinel = fetch_page(last + 1)
        meta["list_requests"] += 1
        meta["pages"] += 1
        meta["post_last_empty_page"] = last + 1
        if not sentinel.structural_empty or sentinel.last != last or sentinel.total != total:
            raise AndongContractError("post-last exact empty boundary changed")
        boundaries = [1] if last == 1 else [1, last]
        with ThreadPoolExecutor(max_workers=len(boundaries)) as pool:
            rechecks = list(pool.map(fetch_page, boundaries))
        meta["list_requests"] += len(rechecks)
        meta["pages"] += len(rechecks)
        meta["boundary_rechecks"] = len(rechecks)
        for recheck in rechecks:
            if _page_signature(recheck) != _page_signature(pages[recheck.requested]):
                raise AndongContractError(f"page {recheck.requested}: boundary stability recheck changed")
        required = last + 1 + len(boundaries)
        meta["required_list_requests"] = required
        listed = [row for page in range(1, last + 1) for row in pages[page].rows]
        identities = [(row["edu_type"], row["identity"]) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            raise AndongContractError(f"{duplicate_count} duplicate compound identities across pages")
        if len(listed) != total:
            raise AndongContractError("complete catalogue cardinality changed")
        list_complete = meta["list_requests"] == required
        fixed_current = [row for row in listed if row["list_end"] is not None and row["list_end"] >= cutoff]
        needs_detail_date = [
            row for row in listed if row["list_end"] is None and row["source_status"] in _ACTIVE_SOURCE
        ]
        candidates = fixed_current + needs_detail_date
        org_registry = {name: code for code, name in first.registry[1][1:]}
        meta.update(
            {
                "data_pages": last,
                "source_total": total,
                "source_rows": len(listed),
                "identity_duplicate_count": duplicate_count,
                "source_type_counts": dict(Counter(row["edu_type"] for row in listed)),
                "source_owner_counts": dict(sorted(Counter(row["owner"] for row in listed).items())),
                "source_status_counts": dict(Counter(row["source_status"] for row in listed)),
                "source_experience_count": sum("체험" in row["title"] for row in listed),
                "fixed_date_current_count": len(fixed_current),
                "status_detail_candidate_count": len(needs_detail_date),
                "current_candidate_count": len(candidates),
                "archived_rows_skipped_before_detail": len(listed) - len(candidates),
                "pagination_complete": list_complete,
                "institution_registry": dict(first.registry[1]),
            }
        )
        if not list_complete:
            raise AndongContractError("list request boundary incomplete")
        if len(candidates) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": f"detail_limit cap allows {detail_limit} of {len(candidates)} current-candidate details",
                }
            )
            return [], ANDONG_PARSER, meta
        meta["detail_attempts"] = len(candidates)

        def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[Optional[dict[str, Any]], str]:
            identity, edu_type = _clean(listed_row.get("identity")), _clean(listed_row.get("edu_type"))
            try:
                soup = _request_soup(andong_detail_url(identity, edu_type), timeout, factory, current_fetcher)
                return _detail_contract(listed_row, soup, cutoff, org_registry), ""
            except Exception as exc:
                return None, f"detail {edu_type}:{identity}: {type(exc).__name__}: {_clean(exc)}"

        contracts: list[dict[str, Any]] = []
        if candidates:
            with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as pool:
                detail_results = list(pool.map(fetch_detail, candidates))
            for contract, error in detail_results:
                if error:
                    errors.append(error)
                    meta["detail_errors"] += 1
                elif contract is not None:
                    contracts.append(contract)
                    meta["detail_pages"] += 1
                    meta["pages"] += 1
        details_complete = not errors and len(contracts) == len(candidates) == meta["detail_pages"]
        detailed_rows = [contract["row"] for contract in contracts if contract.get("row") is not None]
        controls_complete = details_complete and all(
            contract["status"] != "OPEN" or contract["application_control_present"] for contract in contracts
        )
        if details_complete and controls_complete:
            for row in detailed_rows:
                errors.extend(_privacy_errors(row))
            if not errors:
                try:
                    result = list((dedupe_rows or _dedupe_default)(detailed_rows))
                except Exception as exc:
                    errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                if len(result) != len(detailed_rows):
                    errors.append(f"dedupe changed official identity cardinality {len(detailed_rows)} to {len(result)}")
                    result = []
        snapshot_complete = list_complete and details_complete and controls_complete and not errors
        if not snapshot_complete:
            result = []
        current_contracts = [contract for contract in contracts if contract["eligible_current"]]
        meta.update(
            {
                "current_source_count": len(current_contracts),
                "evergreen_current_count": sum(
                    contract["current_basis"] == "on_demand_evergreen" for contract in contracts
                ),
                "experience_count": sum(contract["experience"] for contract in contracts),
                "experience_excluded_count": 0,
                "detail_inactive_or_invalid_count": sum(not contract["eligible_current"] for contract in contracts),
                "detail_exclusion_counts": dict(
                    Counter(contract["exclusion_reason"] for contract in contracts if contract["exclusion_reason"])
                ),
                "branch_counts": dict(sorted(Counter(row["branch"] for row in result).items())),
                "status_counts": dict(Counter(row["status"] for row in result)),
                "domain_category_counts": dict(
                    Counter(row["domain_category"] for row in result)
                ),
                "service_group_counts": dict(
                    Counter(row["service_group"] for row in result)
                ),
                "application_control_count": sum(row["reservation_available"] for row in result),
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not detailed_rows),
                "no_current_reason": "no current/future education or experience programs"
                if snapshot_complete and not detailed_rows
                else "",
                "municipality_coverage": [ANDONG_MUNICIPALITY_CODE],
                "discovery_audit": dict(ANDONG_DISCOVERY_AUDIT),
                "owner_boundary_audit": {key: dict(value) for key, value in ANDONG_OWNER_BOUNDARY_AUDIT.items()},
                "pii_fields_never_persisted": list(ANDONG_PII_FIELDS_NEVER_PERSISTED),
                "pii_payload_persisted": False,
                "forbidden_applicant_endpoint_requests": 0,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return result, ANDONG_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], ANDONG_PARSER, meta


def collect_andong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 300,
    detail_limit: int = 500,
    max_workers: int = ANDONG_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one atomic Andong integrated or library education snapshot."""

    kwargs = {
        "timeout": timeout,
        "max_pages": max_pages,
        "detail_limit": detail_limit,
        "max_workers": max_workers,
        "today": today,
        "session_factory": session_factory,
        "fetcher": fetcher,
        "dedupe_rows": dedupe_rows,
    }
    if _is_andong_library_target(target):
        return _collect_andong_library_education(target, **kwargs)
    return _collect_andong_integrated_education(target, **kwargs)


collect = collect_andong_education


__all__ = [
    "ANDONG_CANONICAL_CANDIDATE_ID",
    "ANDONG_CANONICAL_URL",
    "ANDONG_DISCOVERY_AUDIT",
    "ANDONG_HOST",
    "ANDONG_LIBRARY_CULTURE_CANDIDATE_ID",
    "ANDONG_LIBRARY_CULTURE_PROVIDER",
    "ANDONG_LIBRARY_CULTURE_URL",
    "ANDONG_LIBRARY_EVENT_CANDIDATE_ID",
    "ANDONG_LIBRARY_EVENT_PROVIDER",
    "ANDONG_LIBRARY_EVENT_URL",
    "ANDONG_LIBRARY_PARSER",
    "ANDONG_LIST_PATH",
    "ANDONG_MUNICIPALITY_CODE",
    "ANDONG_MUNICIPALITY_NAME",
    "ANDONG_OWNER_BOUNDARY_AUDIT",
    "ANDONG_PARSER",
    "ANDONG_PII_FIELDS_NEVER_PERSISTED",
    "ANDONG_PROVIDER",
    "ANDONG_REVIEW_FILTER_CANDIDATE_ID",
    "ANDONG_REVIEW_FILTER_PROVIDER",
    "ANDONG_REVIEW_FILTER_URL",
    "AndongContractError",
    "andong_application_url",
    "andong_detail_url",
    "andong_list_url",
    "andong_library_application_url",
    "andong_library_detail_url",
    "andong_library_list_url",
    "collect",
    "collect_andong_education",
    "is_andong_education_target",
    "is_target",
]
