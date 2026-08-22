"""Fail-closed collector for Uiseong County's complete education ledger.

The two search-review candidates are official Uiseong pages, but they are a
general homepage and an administrative organization chart.  Neither page owns
course identities.  The canonical owner is the integrated reservation
service's complete education list at mnu_uid=670.

The collector proves the complete 20-row pagination boundary, the exact empty
sentinel, all official institution and status partitions, and every
current/future detail before publishing an atomic snapshot.  Application
controls are validated but never fetched.  Instructor, manager, phone,
attachments, course body, notices, and applicant data are never persisted.
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


UISEONG_PROVIDER = "MUNI_WWW_USC_GO_KR_AFF8D61A"
UISEONG_CANONICAL_CANDIDATE_ID = "MUNI_IR_35FF73176F56"
UISEONG_NO_WWW_ALIAS_CANDIDATE_ID = "MUNI_IR_2237D9EDE75E"
UISEONG_NO_WWW_ALIAS_PROVIDER = "MUNI_USC_GO_KR_4C334865"
UISEONG_REVIEW_MAIN_CANDIDATE_ID = "MUNI_IR_CD0C088C344B"
UISEONG_REVIEW_MAIN_PROVIDER = "MUNI_USC_GO_KR_10B437E0"
UISEONG_REVIEW_ORG_CANDIDATE_ID = "MUNI_IR_B299B83A3B96"
UISEONG_REVIEW_ORG_PROVIDER = "MUNI_USC_GO_KR_B25337BD"

UISEONG_MUNICIPALITY_CODE = "4773000000"
UISEONG_MUNICIPALITY_NAME = "경상북도 의성군"
UISEONG_HOST = "www.usc.go.kr"
UISEONG_PATH = "/reserve/page.do"
UISEONG_CANONICAL_URL = f"https://{UISEONG_HOST}{UISEONG_PATH}?mnu_uid=670"
UISEONG_PAGE_SIZE = 20
UISEONG_RECOMMENDED_MAX_PAGES = 60
UISEONG_RECOMMENDED_DETAIL_LIMIT = 100
UISEONG_RECOMMENDED_MAX_WORKERS = 3
UISEONG_FETCH_ATTEMPTS = 2
UISEONG_MAX_HTML_BYTES = 3_000_000
UISEONG_EMPTY_SENTINEL = "현재 진행중인 강좌가 없습니다"
UISEONG_AUDITED_REVERSED_EVENT_RANGES: Mapping[str, tuple[str, str]] = {
    # Historic source typo retained by the official ledger; it is expired at
    # the audited cutoff and is never repaired or published as a current row.
    "484": ("2025-02-19", "2024-12-13"),
}
UISEONG_PARSER = (
    "uiseong_integrated_complete_education+advertised_last_and_exact_sentinel+"
    "official_branch_and_status_partition_census+stable_first_last_sentinel+"
    "all_current_details+identity_bound_application_control_no_fetch+"
    "pii_and_free_text_allowlist"
)
UISEONG_OWNERSHIP_SCOPE = (
    "uiseong_integrated_reservation_complete_education_lctre_uid_ledger"
)


class UiseongContractError(ValueError):
    """Raised when the official source no longer satisfies the audited contract."""


@dataclass(frozen=True)
class UiseongBranch:
    code: str
    name: str


UISEONG_BRANCHES: tuple[UiseongBranch, ...] = (
    UiseongBranch("150", "청소년문화의집"),
    UiseongBranch("57", "의성군 평생학습관"),
    UiseongBranch("157", "의성가족센터"),
    UiseongBranch("156", "읍면사무소"),
    UiseongBranch("142", "지질공원"),
    UiseongBranch("131", "펫월드"),
    UiseongBranch("123", "의성군청소년상담복지센터"),
    UiseongBranch("121", "청년정책과"),
    UiseongBranch("116", "관광문화과"),
    UiseongBranch("65", "보건소"),
    UiseongBranch("64", "농업기술센터"),
    UiseongBranch("63", "의성조문국박물관"),
    UiseongBranch("61", "군립도서관"),
)
UISEONG_BRANCH_BY_CODE = {item.code: item for item in UISEONG_BRANCHES}
UISEONG_BRANCH_CODE_BY_NAME = {item.name: item.code for item in UISEONG_BRANCHES}

UISEONG_FIELD_FILTERS: tuple[tuple[str, str], ...] = (
    ("153", "교양/취미"),
    ("154", "인문/사회"),
    ("155", "언어/외국어"),
    ("156", "아동/청소년"),
    ("157", "산업/기술/경제"),
    ("158", "직업능력"),
    ("159", "보건/의료"),
    ("160", "문해교육"),
    ("161", "학력보완"),
    ("162", "주민참여"),
    ("163", "의성읍 경로당"),
    ("164", "농업"),
    ("165", "단촌면 경로당"),
    ("166", "점곡면 경로당"),
    ("167", "옥산면 경로당"),
    ("168", "사곡면 경로당"),
    ("169", "춘산면 경로당"),
    ("170", "가음면 경로당"),
    ("171", "금성면 경로당"),
    ("172", "봉양면 경로당"),
    ("173", "비안면 경로당"),
    ("174", "구천면 경로당"),
    ("175", "단밀면 경로당"),
    ("176", "단북면 경로당"),
    ("177", "안계면 경로당"),
    ("178", "다인면 경로당"),
    ("179", "신평면 경로당"),
    ("180", "안평면 경로당"),
    ("181", "안사면 경로당"),
)
UISEONG_TARGET_FILTERS: tuple[tuple[str, str], ...] = (
    ("808", "유아"),
    ("809", "초등학생"),
    ("810", "중학생"),
    ("811", "초중학생"),
    ("812", "중고등학생"),
    ("813", "고등학생"),
    ("814", "초중고"),
    ("815", "청소년"),
    ("816", "부모/자녀"),
    ("817", "여성"),
    ("818", "남성"),
    ("819", "농축산업인"),
    ("820", "다문화"),
    ("821", "장애인"),
    ("822", "성인"),
    ("823", "노인"),
    ("824", "통합"),
    ("825", "기타"),
)
UISEONG_STATUS_FILTERS: tuple[tuple[str, str], ...] = (
    ("A", "접수대기"),
    ("B", "접수중"),
    ("C", "접수마감"),
)
UISEONG_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
UISEONG_STATUS_CLASS: Mapping[str, str] = {
    "접수대기": "status_2",
    "접수중": "status_3",
    "접수마감": "status_4",
}

UISEONG_AUDITED_CATEGORIES = frozenset(
    {
        "미술/공예/수예",
        "스포츠/건강",
        "기타",
        "요리",
        "음악/노래",
        "자격증",
        "귀농전문교육",
        "음악",
        "농업인교육",
        "미술",
        "독서",
        "무용",
        "영어",
        "예능",
        "역사",
        "과학",
        "글쓰기",
        "철학",
        "연극/영화",
        "문예 창작",
        "서예",
        "수학",
        "새해농업인실용교육",
        "청년농업인 스마트팜 딸기아카데미교육",
        "교육",
        "청년농업인교육",
        "독서지도",
        "농업인정보화교육",
        "재테크",
    }
)

UISEONG_DETAIL_LABELS: tuple[str, ...] = (
    "교육명",
    "접수 일시",
    "교육 일시",
    "교육 요일",
    "장소",
    "교육대상",
    "1회 교육시간",
    "교육횟수",
    "모집인원",
    "수강료",
    "재료",
    "재료비",
    "강사명",
    "지역",
    "담당자",
    "문의전화",
    "교육내용",
    "주의사항",
    "첨부파일",
)
UISEONG_DISCARDED_DETAIL_FIELDS: tuple[str, ...] = (
    "재료",
    "강사명",
    "담당자",
    "문의전화",
    "교육내용",
    "주의사항",
    "첨부파일",
)

UISEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    UISEONG_CANONICAL_CANDIDATE_ID: {
        "provider": UISEONG_PROVIDER,
        "url": UISEONG_CANONICAL_URL,
        "decision": "promote_complete_official_education_lctre_uid_ledger",
    },
    UISEONG_REVIEW_MAIN_CANDIDATE_ID: {
        "provider": UISEONG_REVIEW_MAIN_PROVIDER,
        "url": "https://usc.go.kr/ko/main.do",
        "decision": "exclude_general_homepage_without_course_identity_ledger",
    },
    UISEONG_REVIEW_ORG_CANDIDATE_ID: {
        "provider": UISEONG_REVIEW_ORG_PROVIDER,
        "url": "https://usc.go.kr/ko/page.do?mnu_uid=401",
        "decision": "exclude_administrative_organization_chart_without_courses",
    },
    UISEONG_NO_WWW_ALIAS_CANDIDATE_ID: {
        "provider": UISEONG_NO_WWW_ALIAS_PROVIDER,
        "url": "https://usc.go.kr/reserve/page.do?mnu_uid=670",
        "decision": "exclude_no_www_duplicate_alias_of_canonical_owner",
    },
}

UISEONG_OWNER_BOUNDARIES: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.usc.go.kr/lecture/page.do?mnu_uid=82",
        "decision": "exclude_complete_duplicate_filter_alias_srchSite_57",
        "owner": UISEONG_PROVIDER,
    },
    {
        "url": "https://www.usc.go.kr/library/index.do",
        "decision": "exclude_incomplete_homepage_subset_linking_canonical_srchSite_61",
        "owner": UISEONG_PROVIDER,
    },
    {
        "url": (
            "https://www.usc.go.kr/reserve/page.do?"
            "mnu_uid=671&srchTrgts=808,809,810,811,812,813,814,815"
        ),
        "decision": "exclude_youth_target_filter_alias_of_canonical_identity_set",
        "owner": UISEONG_PROVIDER,
    },
    {
        "url": "https://www.usc.go.kr/jmgmuseum",
        "decision": "exclude_information_home; museum_applications_are_in_canonical_ledger",
        "owner": UISEONG_PROVIDER,
    },
    {
        "url": (
            "https://www.gbelib.kr/us/module/teach/index.do?"
            "menu_idx=151&searchCate1=16%2C17"
        ),
        "decision": "exclude_separate_gyeongbuk_education_office_library_owner",
        "owner": "GBELIB_UISEONG_LIBRARY_SEPARATE_OWNER",
    },
    {
        "url": "https://www.usc.go.kr/reserve/page.do?mnu_uid=672",
        "decision": "exclude_separate_event_reservation_service",
        "owner": "",
    },
    {
        "url": "https://www.usc.go.kr/reserve/page.do?mnu_uid=673",
        "decision": "exclude_separate_lodging_and_camping_service",
        "owner": "",
    },
    {
        "url": "https://www.usc.go.kr/reserve/page.do?mnu_uid=676",
        "decision": "exclude_separate_facility_and_equipment_service",
        "owner": "",
    },
    {
        "url": "https://www.usc.go.kr/share/reserveList.do",
        "decision": "exclude_separate_shared_facility_reservation_service",
        "owner": "",
    },
)

UISEONG_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "cutoff": "2026-07-23",
    "data_pages": 39,
    "sentinel_page": 40,
    "source_rows": 778,
    "source_capacity_shape_counts": {
        "current_total_and_waitlist": 560,
        "legacy_applied_without_capacity": 35,
        "legacy_confirmed_without_capacity": 183,
    },
    "source_reversed_date_anomaly_count": 1,
    "source_status_counts": {"접수마감": 764, "접수중": 13, "접수대기": 1},
    "current_rows": 38,
    "current_status_counts": {"CLOSED": 24, "OPEN": 13, "SCHEDULED": 1},
    "detail_pages": 38,
    "application_controls": 13,
    "expected_requests": 105,
}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE = re.compile(r"\s+")
_IDENTITY = re.compile(r"^[1-9]\d*$")
_DATE_RANGE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_DETAIL_DATES = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_APPLY_DATETIME = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*(\d{1,2})시\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*(\d{1,2})시$"
)
_CAPACITY = re.compile(r"^신청정원\s*:\s*(\d+)\s*/\s*후보정원\s*:\s*(\d+)$")
_COUNT = re.compile(r"^(신청|후보)\s*(\d[\d,]*)\s*/\s*(\d[\d,]*)명$")
_LEGACY_COUNT = re.compile(r"^(신청|확정)\s*(\d[\d,]*)$")
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
        "source_target",
        "source_schedule",
        "source_weekdays",
        "source_venue",
        "source_region",
        "session_hours",
        "session_count",
        "waitlist_current",
        "waitlist_total",
        "detail_verified",
        "application_control_present",
        "application_control_verified",
        "application_endpoint_fetched",
        "applicant_endpoint_fetched",
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
        "instructor",
        "instructor_name",
        "manager",
        "manager_name",
        "attachments",
        "attachment_urls",
        "course_content",
        "detail_description",
        "notice",
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


def is_uiseong_education_target(target: Any) -> bool:
    """Match only the promoted www canonical owner and exact menu query."""

    if _clean(_target_value(target, "provider")) != UISEONG_PROVIDER:
        return False
    url = _clean(_target_value(target, "url"))
    if url != UISEONG_CANONICAL_URL:
        return False
    try:
        parsed = urlparse(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() == UISEONG_HOST
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == UISEONG_PATH
        and query == [("mnu_uid", "670")]
        and not parsed.fragment
    )


is_target = is_uiseong_education_target


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


def _list_url(
    page: int = 1,
    *,
    branch_code: str = "",
    status_code: str = "",
) -> str:
    query: list[tuple[str, str]] = [("mnu_uid", "670")]
    if page > 1:
        query.append(("pageNo", str(page)))
    if branch_code:
        query.append(("srchSite", branch_code))
    if status_code:
        query.append(("srchStts", status_code))
    return f"https://{UISEONG_HOST}{UISEONG_PATH}?{urlencode(query)}"


def _detail_url(identity: str) -> str:
    return (
        f"https://{UISEONG_HOST}{UISEONG_PATH}?"
        + urlencode((("cmd", "2"), ("mnu_uid", "670"), ("lctre_uid", identity)))
    )


def _application_url(identity: str) -> str:
    return (
        f"https://{UISEONG_HOST}{UISEONG_PATH}?"
        + urlencode(
            (
                ("cmd", "4"),
                ("pageNo", ""),
                ("mnu_uid", "670"),
                ("lctre_uid", identity),
            )
        )
    )


def _same_response_url(actual: str, expected: str) -> bool:
    try:
        left = urlparse(actual)
        right = urlparse(expected)
        return bool(
            left.scheme == right.scheme == "https"
            and (left.hostname or "").lower() == (right.hostname or "").lower() == UISEONG_HOST
            and left.port is None
            and right.port is None
            and left.username is None
            and left.password is None
            and left.path == right.path == UISEONG_PATH
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
    for attempt in range(1, UISEONG_FETCH_ATTEMPTS + 1):
        try:
            response = fetcher(session, url, timeout)
            if getattr(response, "status_code", None) != 200:
                raise UiseongContractError(
                    f"HTTP {getattr(response, 'status_code', None)} for {url}"
                )
            if getattr(response, "history", None):
                raise UiseongContractError(f"redirect is not allowed for {url}")
            response_url = _clean(getattr(response, "url", ""))
            if not _same_response_url(response_url, url):
                raise UiseongContractError(
                    f"response URL drift: expected {url}, received {response_url}"
                )
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", "")).encode("utf-8")
            if len(content) > UISEONG_MAX_HTML_BYTES:
                raise UiseongContractError(f"HTML exceeds byte limit for {url}")
            return BeautifulSoup(content, "html.parser"), attempt
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _options(soup: BeautifulSoup, selector: str) -> tuple[tuple[str, str], ...]:
    select = soup.select_one(selector)
    if select is None:
        raise UiseongContractError(f"missing selector {selector}")
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in select.find_all("option", recursive=False)
    )


def _selected_value(soup: BeautifulSoup, selector: str) -> str:
    selected = soup.select(f"{selector} option[selected]")
    if len(selected) > 1:
        raise UiseongContractError(f"multiple selected options in {selector}")
    return _clean(selected[0].get("value")) if selected else ""


def _validate_form_contract(
    soup: BeautifulSoup,
    *,
    branch_code: str,
    status_code: str,
) -> None:
    form = soup.select_one("form#frm")
    if form is None or _clean(form.get("method")).lower() != "post":
        raise UiseongContractError("missing POST form#frm")
    expected_inputs = {
        "mnu_uid": "670",
        "returnUrl": "/reserve/page.do",
        "queryString": "mnu_uid=670&",
        "cmd": "",
    }
    for name, expected in expected_inputs.items():
        node = form.select_one(f'input[name="{name}"]')
        if node is None or _clean(node.get("value")) != expected:
            raise UiseongContractError(f"form input {name} drift")

    expected_options = {
        "#srchSite": (("", "기관전체"),)
        + tuple((item.code, item.name) for item in UISEONG_BRANCHES),
        "#srchFld_parents": (("", "분야전체"),) + UISEONG_FIELD_FILTERS,
        "#srchTrgt": (("", "교육대상전체"),) + UISEONG_TARGET_FILTERS,
        "#srchStts": (("", "전체"),) + UISEONG_STATUS_FILTERS,
    }
    for selector, expected in expected_options.items():
        if _options(soup, selector) != expected:
            raise UiseongContractError(f"official option vocabulary drift in {selector}")
    if _options(soup, "#srchFld"):
        raise UiseongContractError("dependent #srchFld must initially be empty")
    if _selected_value(soup, "#srchSite") != branch_code:
        raise UiseongContractError("branch filter selection was not preserved")
    if _selected_value(soup, "#srchStts") != status_code:
        raise UiseongContractError("status filter selection was not preserved")


def _parse_range(value: str, label: str, identity: str = "") -> tuple[date, date]:
    match = _DATE_RANGE.fullmatch(_clean(value))
    if not match:
        raise UiseongContractError(f"{label} range shape drift: {_clean(value)}")
    start, end = (date.fromisoformat(token) for token in match.groups())
    if end < start and (
        label != "education"
        or UISEONG_AUDITED_REVERSED_EVENT_RANGES.get(identity)
        != (start.isoformat(), end.isoformat())
    ):
        raise UiseongContractError(f"{label} range is reversed")
    return start, end


def _course_href(anchor: Any) -> tuple[str, str]:
    href = _clean(anchor.get("href"))
    try:
        parsed = urlparse(href)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise UiseongContractError("malformed course detail link") from exc
    if parsed.scheme or parsed.netloc or parsed.path not in {"", UISEONG_PATH}:
        raise UiseongContractError("course detail link escaped the canonical path")
    if len(query) != 3 or dict(query).keys() != {"cmd", "mnu_uid", "lctre_uid"}:
        raise UiseongContractError("course detail query shape drift")
    values = dict(query)
    identity = values["lctre_uid"]
    if (
        values["cmd"] != "2"
        or values["mnu_uid"] != "670"
        or not _IDENTITY.fullmatch(identity)
    ):
        raise UiseongContractError("invalid course identity link")
    return identity, _detail_url(identity)


def _parse_row(node: Any, page: int, sequence: int) -> dict[str, Any]:
    anchor = node.find("a", recursive=False)
    if anchor is None:
        raise UiseongContractError("course row lacks direct detail anchor")
    identity, detail_url = _course_href(anchor)
    category_node = anchor.select_one("span.type")
    branch_node = anchor.select_one("span.org")
    title_node = anchor.select_one("p.tit")
    if category_node is None or branch_node is None or title_node is None:
        raise UiseongContractError(f"course {identity}: list identity fields missing")
    category = _clean(category_node.get_text(" ", strip=True))
    branch = _clean(branch_node.get_text(" ", strip=True))
    title = _clean(title_node.get_text(" ", strip=True))
    if category not in UISEONG_AUDITED_CATEGORIES:
        raise UiseongContractError(f"course {identity}: unaudited category {category}")
    if branch not in UISEONG_BRANCH_CODE_BY_NAME:
        raise UiseongContractError(f"course {identity}: unknown official branch {branch}")
    if not title:
        raise UiseongContractError(f"course {identity}: empty title")

    facts = [_clean(item.get_text(" ", strip=True)) for item in anchor.select("ul.dep_02 > li")]
    if len(facts) != 3:
        raise UiseongContractError(f"course {identity}: list fact count drift")
    values: dict[str, str] = {}
    for value, expected_label in zip(facts, ("신청", "교육", "교육대상")):
        if ":" not in value:
            raise UiseongContractError(f"course {identity}: malformed {expected_label} fact")
        label, text = (_clean(part) for part in value.split(":", 1))
        if label != expected_label or not text:
            raise UiseongContractError(f"course {identity}: {expected_label} fact drift")
        values[label] = text
    apply_start, apply_end = _parse_range(values["신청"], "application", identity)
    event_start, event_end = _parse_range(values["교육"], "education", identity)
    target = values["교육대상"]
    if target not in {name for _, name in UISEONG_TARGET_FILTERS}:
        raise UiseongContractError(f"course {identity}: unknown education target {target}")

    count_nodes = node.select(":scope > ul.num > li")
    if len(count_nodes) != 2:
        raise UiseongContractError(f"course {identity}: applicant count shape drift")
    count_texts = [_clean(item.get_text(" ", strip=True)) for item in count_nodes]
    modern = [_COUNT.fullmatch(value) for value in count_texts]
    if all(modern):
        counts: dict[str, tuple[int, int]] = {}
        for match in modern:
            assert match is not None
            label, current, total = match.groups()
            if label in counts:
                raise UiseongContractError(f"course {identity}: repeated applicant count")
            counts[label] = (int(current.replace(",", "")), int(total.replace(",", "")))
        if set(counts) != {"신청", "후보"}:
            raise UiseongContractError(f"course {identity}: applicant labels drift")
        capacity_current: Optional[int] = counts["신청"][0]
        capacity_total: Optional[int] = counts["신청"][1]
        waitlist_current: Optional[int] = counts["후보"][0]
        waitlist_total: Optional[int] = counts["후보"][1]
        capacity_shape = "current_total_and_waitlist"
    else:
        nonempty = [value for value in count_texts if value]
        legacy = _LEGACY_COUNT.fullmatch(nonempty[0]) if len(nonempty) == 1 else None
        if legacy is None:
            raise UiseongContractError(f"course {identity}: malformed applicant count")
        legacy_label, legacy_current = legacy.groups()
        capacity_current = int(legacy_current.replace(",", ""))
        capacity_total = None
        waitlist_current = None
        waitlist_total = None
        capacity_shape = (
            "legacy_confirmed_without_capacity"
            if legacy_label == "확정"
            else "legacy_applied_without_capacity"
        )

    status_node = node.select_one(":scope > div.status > span")
    if status_node is None:
        raise UiseongContractError(f"course {identity}: status missing")
    raw_status = _clean(status_node.get_text(" ", strip=True))
    if raw_status not in UISEONG_STATUS_MAP:
        raise UiseongContractError(f"course {identity}: unknown status {raw_status}")
    classes = set(status_node.get("class") or ())
    if classes != {UISEONG_STATUS_CLASS[raw_status]}:
        raise UiseongContractError(f"course {identity}: status class drift")

    return {
        "identity": identity,
        "detail_url": detail_url,
        "page": page,
        "sequence": sequence,
        "title": title,
        "category": category,
        "branch": branch,
        "branch_code": UISEONG_BRANCH_CODE_BY_NAME[branch],
        "target": target,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "event_start": event_start,
        "event_end": event_end,
        "reversed_event_range_anomaly": event_end < event_start,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": waitlist_current,
        "waitlist_total": waitlist_total,
        "capacity_shape": capacity_shape,
        "raw_status": raw_status,
        "status": UISEONG_STATUS_MAP[raw_status],
    }


def _advertised_last(soup: BeautifulSoup) -> int:
    paging = soup.select_one("div.paging")
    if paging is None:
        raise UiseongContractError("missing div.paging")
    numbers: list[int] = []
    current = paging.select_one('strong[title="현재 페이지"]')
    if current is not None:
        value = _clean(current.get_text(" ", strip=True))
        if not value.isdigit():
            raise UiseongContractError("malformed current page marker")
        numbers.append(int(value))
    for anchor in paging.select("a[href]"):
        href = _clean(anchor.get("href"))
        try:
            values = dict(parse_qsl(urlparse(href).query, keep_blank_values=True))
        except ValueError as exc:
            raise UiseongContractError("malformed pager link") from exc
        if "pageNo" in values:
            if not values["pageNo"].isdigit():
                raise UiseongContractError("non-numeric pager pageNo")
            numbers.append(int(values["pageNo"]))
    return max(numbers, default=1)


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
    *,
    branch_code: str = "",
    status_code: str = "",
) -> dict[str, Any]:
    _validate_form_contract(soup, branch_code=branch_code, status_code=status_code)
    container = soup.select_one("div.applyList > ul")
    if container is None:
        raise UiseongContractError("missing div.applyList list")
    direct_items = container.find_all("li", recursive=False)
    course_items = [item for item in direct_items if item.find("a", recursive=False) is not None]
    sentinel = (
        len(direct_items) == 1
        and not course_items
        and _clean(direct_items[0].get_text(" ", strip=True)) == UISEONG_EMPTY_SENTINEL
    )
    if not course_items:
        if not sentinel:
            raise UiseongContractError("empty page lacks exact official sentinel")
        rows: list[dict[str, Any]] = []
    else:
        if len(course_items) != len(direct_items):
            raise UiseongContractError("course rows mixed with non-course list items")
        if len(course_items) > UISEONG_PAGE_SIZE:
            raise UiseongContractError("page exceeds audited 20-row size")
        rows = [_parse_row(item, page, index) for index, item in enumerate(course_items, 1)]
        identities = [int(row["identity"]) for row in rows]
        if any(left <= right for left, right in zip(identities, identities[1:])):
            raise UiseongContractError("course identities are not strictly descending on page")

    paging = soup.select_one("div.paging")
    if paging is None:
        raise UiseongContractError("missing pagination contract")
    current = paging.select_one('strong[title="현재 페이지"]')
    if rows:
        if current is None or _clean(current.get_text(" ", strip=True)) != str(page):
            raise UiseongContractError(f"page {page}: current page marker drift")
    elif branch_code or status_code:
        if current is None or _clean(current.get_text(" ", strip=True)) != "1":
            raise UiseongContractError("empty filter lacks its page-one marker")
    elif current is not None:
        raise UiseongContractError("post-boundary sentinel unexpectedly has current page marker")
    return {
        "page": page,
        "rows": rows,
        "empty": sentinel,
        "advertised_last": _advertised_last(soup),
        "branch_code": branch_code,
        "status_code": status_code,
    }


def _page_signature(parsed: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(parsed["page"]),
        bool(parsed["empty"]),
        int(parsed["advertised_last"]),
        tuple(
            (
                row["identity"],
                row["title"],
                row["category"],
                row["branch"],
                row["target"],
                row["apply_start"].isoformat(),
                row["apply_end"].isoformat(),
                row["event_start"].isoformat(),
                row["event_end"].isoformat(),
                row["reversed_event_range_anomaly"],
                row["capacity_total"],
                row["waitlist_total"],
                row["capacity_shape"],
                row["raw_status"],
            )
            for row in parsed["rows"]
        ),
    )


def _detail_fields(soup: BeautifulSoup, identity: str) -> dict[str, str]:
    container = soup.select_one("div.class-lst")
    if container is None:
        raise UiseongContractError(f"course {identity}: missing detail field list")
    fields: dict[str, str] = {}
    labels: list[str] = []
    for block in container.find_all("dl", recursive=False):
        dt = block.find("dt", recursive=False)
        dd = block.find("dd", recursive=False)
        if dt is None or dd is None:
            raise UiseongContractError(f"course {identity}: malformed detail field")
        label = _clean(dt.get_text(" ", strip=True))
        if label in fields:
            raise UiseongContractError(f"course {identity}: repeated detail field {label}")
        labels.append(label)
        fields[label] = _clean(dd.get_text(" ", strip=True))
    if tuple(labels) != UISEONG_DETAIL_LABELS:
        raise UiseongContractError(f"course {identity}: detail field vocabulary/order drift")
    return fields


def _parse_apply_datetime(value: str, identity: str) -> tuple[date, int, date, int]:
    match = _APPLY_DATETIME.fullmatch(_clean(value))
    if not match:
        raise UiseongContractError(f"course {identity}: application datetime shape drift")
    start_text, start_hour_text, end_text, end_hour_text = match.groups()
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    start_hour = int(start_hour_text)
    end_hour = int(end_hour_text)
    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
        raise UiseongContractError(f"course {identity}: invalid application hour")
    if (end, end_hour) < (start, start_hour):
        raise UiseongContractError(f"course {identity}: reversed application datetime")
    return start, start_hour, end, end_hour


def _validate_application_href(href: str, identity: str, detail_url: str) -> str:
    absolute = urljoin(detail_url, href)
    try:
        parsed = urlparse(absolute)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise UiseongContractError(f"course {identity}: malformed application href") from exc
    expected = [
        ("cmd", "4"),
        ("pageNo", ""),
        ("mnu_uid", "670"),
        ("lctre_uid", identity),
    ]
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != UISEONG_HOST
        or parsed.port is not None
        or parsed.path != UISEONG_PATH
        or query != expected
        or parsed.fragment
    ):
        raise UiseongContractError(f"course {identity}: application href identity drift")
    expected_url = _application_url(identity)
    if absolute != expected_url:
        raise UiseongContractError(f"course {identity}: application URL encoding drift")
    return absolute


def _validate_list_href(href: str, identity: str, detail_url: str) -> None:
    absolute = urljoin(detail_url, href)
    try:
        parsed = urlparse(absolute)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError as exc:
        raise UiseongContractError(f"course {identity}: malformed return-to-list href") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != UISEONG_HOST
        or parsed.port is not None
        or parsed.path != UISEONG_PATH
        or query != [("pageNo", ""), ("mnu_uid", "670")]
        or parsed.query != "pageNo=&mnu_uid=670&"
        or parsed.fragment
    ):
        raise UiseongContractError(f"course {identity}: return-to-list href drift")


def _control(
    soup: BeautifulSoup,
    identity: str,
    detail_url: str,
    raw_status: str,
) -> tuple[bool, str]:
    controls = soup.select("div.lectureBtn > a")
    if len(controls) != 2:
        raise UiseongContractError(f"course {identity}: detail control count drift")
    action, back = controls
    if _clean(back.get_text(" ", strip=True)) != "목록" or set(back.get("class") or ()) != {
        "btn",
        "list",
        "big",
    }:
        raise UiseongContractError(f"course {identity}: list control drift")
    _validate_list_href(_clean(back.get("href")), identity, detail_url)

    text = _clean(action.get_text(" ", strip=True))
    classes = set(action.get("class") or ())
    href = _clean(action.get("href"))
    if raw_status == "접수중":
        if text != "신청하기" or classes != {"btn_write", "deadline", "big"} or not href:
            raise UiseongContractError(f"course {identity}: open application control drift")
        return True, _validate_application_href(href, identity, detail_url)
    if text != raw_status or classes != {"btn", "deadline", "big"}:
        raise UiseongContractError(f"course {identity}: inactive control/status drift")
    if href or not action.has_attr("disabled"):
        raise UiseongContractError(f"course {identity}: inactive control is actionable")
    return False, ""


def _money_amount(value: str) -> Optional[int]:
    cleaned = _clean(value).replace(",", "").removesuffix("원").strip()
    return int(cleaned) if cleaned.isdigit() else None


def _parse_detail(
    listed: Mapping[str, Any],
    soup: BeautifulSoup,
    cutoff: date,
) -> dict[str, Any]:
    identity = str(listed["identity"])
    fields = _detail_fields(soup, identity)
    if fields["교육명"] != listed["title"]:
        raise UiseongContractError(f"course {identity}: list/detail title drift")

    apply_start, apply_start_hour, apply_end, apply_end_hour = _parse_apply_datetime(
        fields["접수 일시"], identity
    )
    if apply_start != listed["apply_start"] or apply_end != listed["apply_end"]:
        raise UiseongContractError(f"course {identity}: list/detail application period drift")
    event_dates = _DETAIL_DATES.findall(fields["교육 일시"])
    if len(event_dates) != 2:
        raise UiseongContractError(f"course {identity}: education datetime shape drift")
    event_start, event_end = (date.fromisoformat(value) for value in event_dates)
    if event_start != listed["event_start"] or event_end != listed["event_end"]:
        raise UiseongContractError(f"course {identity}: list/detail education period drift")
    if fields["교육대상"] != listed["target"]:
        raise UiseongContractError(f"course {identity}: list/detail target drift")

    capacity_match = _CAPACITY.fullmatch(fields["모집인원"])
    if not capacity_match:
        raise UiseongContractError(f"course {identity}: detail capacity shape drift")
    capacity_total, waitlist_total = (int(value) for value in capacity_match.groups())
    listed_capacity_total = listed["capacity_total"]
    listed_waitlist_total = listed["waitlist_total"]
    if (
        listed_capacity_total is not None
        and capacity_total != listed_capacity_total
    ) or (
        listed_waitlist_total is not None
        and waitlist_total != listed_waitlist_total
    ):
        raise UiseongContractError(f"course {identity}: list/detail capacity drift")
    waitlist_current = listed["waitlist_current"]

    raw_status = str(listed["raw_status"])
    status = str(listed["status"])
    if status == "SCHEDULED" and not cutoff < apply_start:
        raise UiseongContractError(f"course {identity}: scheduled status/date disagreement")
    if status == "OPEN" and not apply_start <= cutoff <= apply_end:
        raise UiseongContractError(f"course {identity}: open status/date disagreement")
    control_present, application_url = _control(
        soup, identity, str(listed["detail_url"]), raw_status
    )
    if control_present != (status == "OPEN"):
        raise UiseongContractError(f"course {identity}: application control/status mismatch")

    venue = fields["장소"]
    target = fields["교육대상"]
    schedule = fields["교육 일시"]
    weekdays = fields["교육 요일"]
    region = fields["지역"]
    if not venue or not schedule or not target:
        raise UiseongContractError(f"course {identity}: venue/schedule/target missing")
    safe_detail_values = (venue, target, schedule, weekdays, region)
    if any(
        _PHONE.search(value) or _EMAIL.search(value) or _RESIDENT_ID.search(value)
        for value in safe_detail_values
    ):
        raise UiseongContractError(f"course {identity}: safe detail field contains PII")

    detail_url = str(listed["detail_url"])
    apply_period = f"{apply_start.isoformat()} ~ {apply_end.isoformat()}"
    event_period = f"{event_start.isoformat()} ~ {event_end.isoformat()}"
    return {
        "provider": UISEONG_PROVIDER,
        "provider_course_id": f"{UISEONG_PROVIDER}:lctre:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": str(listed["title"]),
        "description": str(listed["title"]),
        "branch": str(listed["branch"]),
        "branch_code": str(listed["branch_code"]),
        "branch_url": _list_url(branch_code=str(listed["branch_code"])),
        "preserve_branch": True,
        "category": str(listed["category"]),
        "program_type": "교육",
        "raw_url": detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if status == "OPEN" else "INFO_ONLY",
        "application_method": "온라인" if status == "OPEN" else "안내",
        "application_methods": ["온라인"] if status == "OPEN" else ["안내"],
        "reservation_available": control_present,
        "status": status,
        "raw_status": raw_status,
        "fee": fields["수강료"],
        "fee_amount": _money_amount(fields["수강료"]),
        "material_fee": fields["재료비"],
        "material_fee_amount": _money_amount(fields["재료비"]),
        "period": event_period,
        "start_date": event_start.isoformat(),
        "end_date": event_end.isoformat(),
        "apply_period": apply_period,
        "apply_start_date": apply_start.isoformat(),
        "apply_end_date": apply_end.isoformat(),
        "apply_start_hour": apply_start_hour,
        "apply_end_hour": apply_end_hour,
        "schedule_raw": schedule,
        "capacity": f"{capacity_total}명",
        "capacity_current": int(listed["capacity_current"]),
        "capacity_total": capacity_total,
        "capacity_remaining": max(capacity_total - int(listed["capacity_current"]), 0),
        "waitlist_current": (
            int(waitlist_current) if waitlist_current is not None else None
        ),
        "waitlist_total": waitlist_total,
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "room": venue,
        "address": "",
        "venue_address": "",
        "region": region,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": UISEONG_PARSER,
        "municipality_code": UISEONG_MUNICIPALITY_CODE,
        "municipality_full_name": UISEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed["page"]),
            "source_sequence": int(listed["sequence"]),
            "source_status": raw_status,
            "source_category": str(listed["category"]),
            "source_branch": str(listed["branch"]),
            "source_apply_period": fields["접수 일시"],
            "source_education_period": event_period,
            "source_target": target,
            "source_schedule": schedule,
            "source_weekdays": weekdays,
            "source_venue": venue,
            "source_region": region,
            "session_hours": fields["1회 교육시간"],
            "session_count": fields["교육횟수"],
            "waitlist_current": (
                int(waitlist_current) if waitlist_current is not None else None
            ),
            "waitlist_total": waitlist_total,
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_verified": True,
            "application_endpoint_fetched": False,
            "applicant_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "discarded_detail_fields": list(UISEONG_DISCARDED_DETAIL_FIELDS),
            "address_policy": "venue_name_only_no_official_street_address",
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
        errors.append("free-form description persisted")
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
        _clean(row.get("target")),
    )


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": UISEONG_MUNICIPALITY_CODE,
        "municipality_full_name": UISEONG_MUNICIPALITY_NAME,
        "owner_provider": UISEONG_PROVIDER,
        "canonical_provider": UISEONG_PROVIDER,
        "canonical_candidate_id": UISEONG_CANONICAL_CANDIDATE_ID,
        "canonical_url": UISEONG_CANONICAL_URL,
        "review_candidate_ids": [
            UISEONG_REVIEW_MAIN_CANDIDATE_ID,
            UISEONG_REVIEW_ORG_CANDIDATE_ID,
        ],
        "candidate_audit": {key: dict(value) for key, value in UISEONG_CANDIDATE_AUDIT.items()},
        "provider_override": {
            "from": [UISEONG_REVIEW_MAIN_PROVIDER, UISEONG_REVIEW_ORG_PROVIDER],
            "to": UISEONG_PROVIDER,
            "reason": "review candidates have no course identity ledger",
        },
        "owner_boundaries": [dict(item) for item in UISEONG_OWNER_BOUNDARIES],
        "ownership_scope": UISEONG_OWNERSHIP_SCOPE,
        "parser": UISEONG_PARSER,
        "page_size": UISEONG_PAGE_SIZE,
        "recommended_max_pages": UISEONG_RECOMMENDED_MAX_PAGES,
        "recommended_detail_limit": UISEONG_RECOMMENDED_DETAIL_LIMIT,
        "recommended_max_workers": UISEONG_RECOMMENDED_MAX_WORKERS,
        "recommended_timeout_seconds": 30,
        "fetch_attempts": UISEONG_FETCH_ATTEMPTS,
        "max_html_bytes": UISEONG_MAX_HTML_BYTES,
        "live_audit_baseline": dict(UISEONG_LIVE_AUDIT_BASELINE),
        "address_policy": "detail_venue_name_only; no official per-course street address",
        "pii_policy": "discard instructor/manager/phone/applicant data and free-text/file bodies",
        "source_requests": 0,
        "request_attempts": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "application_endpoints_called": 0,
        "applicant_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "data_pages": 0,
        "page_counts": [],
        "advertised_last_page": 0,
        "sentinel_page": 0,
        "sentinel_verified": False,
        "page1_rechecked": False,
        "last_page_rechecked": False,
        "sentinel_rechecked": False,
        "boundary_rechecks": 0,
        "branch_filter_counts": {},
        "branch_filter_pages": {},
        "status_filter_counts": {},
        "status_filter_pages": {},
        "filter_census_complete": False,
        "source_rows": 0,
        "source_capacity_shape_counts": {},
        "source_reversed_date_anomaly_count": 0,
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


def _fetch_parsed_list(
    session: Any,
    page: int,
    timeout: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
    *,
    branch_code: str = "",
    status_code: str = "",
) -> dict[str, Any]:
    url = _list_url(page, branch_code=branch_code, status_code=status_code)
    soup, attempts = _fetch_soup(session, url, timeout, fetcher)
    meta["source_requests"] += 1
    meta["list_requests"] += 1
    meta["request_attempts"] += attempts
    return _parse_list_page(
        soup, page, branch_code=branch_code, status_code=status_code
    )


def _filtered_count(
    session: Any,
    timeout: int,
    max_pages: int,
    fetcher: Fetcher,
    meta: dict[str, Any],
    *,
    branch_code: str = "",
    status_code: str = "",
) -> tuple[int, int, list[dict[str, Any]]]:
    first = _fetch_parsed_list(
        session,
        1,
        timeout,
        fetcher,
        meta,
        branch_code=branch_code,
        status_code=status_code,
    )
    last = int(first["advertised_last"])
    if last > max_pages:
        meta["source_cap_reached"] = True
        raise UiseongContractError(
            f"filtered advertised last page {last} exceeds max_pages {max_pages}"
        )
    if first["empty"]:
        if last != 1:
            raise UiseongContractError("empty filter advertises multiple pages")
        return 0, 1, []
    if last == 1:
        return len(first["rows"]), 1, list(first["rows"])
    if len(first["rows"]) != UISEONG_PAGE_SIZE:
        raise UiseongContractError("multi-page filter first page is not full")
    final = _fetch_parsed_list(
        session,
        last,
        timeout,
        fetcher,
        meta,
        branch_code=branch_code,
        status_code=status_code,
    )
    if final["empty"] or not 1 <= len(final["rows"]) <= UISEONG_PAGE_SIZE:
        raise UiseongContractError("multi-page filter final page boundary drift")
    if int(final["advertised_last"]) != last:
        raise UiseongContractError("filtered first/final advertised page drift")
    count = (last - 1) * UISEONG_PAGE_SIZE + len(final["rows"])
    return count, last, list(first["rows"]) + list(final["rows"])


def _validate_filter_edges(
    edge_rows: Iterable[Mapping[str, Any]],
    source_by_id: Mapping[str, Mapping[str, Any]],
    *,
    branch_code: str = "",
    status_code: str = "",
) -> None:
    expected_status = dict(UISEONG_STATUS_FILTERS).get(status_code, "")
    for row in edge_rows:
        identity = str(row["identity"])
        source = source_by_id.get(identity)
        if source is None or _page_signature(
            {"page": 1, "empty": False, "advertised_last": 1, "rows": [row]}
        )[3] != _page_signature(
            {"page": 1, "empty": False, "advertised_last": 1, "rows": [source]}
        )[3]:
            raise UiseongContractError("filter edge row does not match canonical source row")
        if branch_code and row["branch_code"] != branch_code:
            raise UiseongContractError("branch filter returned another branch")
        if status_code and row["raw_status"] != expected_status:
            raise UiseongContractError("status filter returned another status")


def collect_uiseong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = UISEONG_RECOMMENDED_MAX_PAGES,
    detail_limit: int = UISEONG_RECOMMENDED_DETAIL_LIMIT,
    max_workers: int = UISEONG_RECOMMENDED_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    allow_raw_requests_for_tests: bool = False,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete, stable, privacy-safe Uiseong education snapshot."""

    meta = _initial_meta()
    if not is_uiseong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match exact canonical Uiseong education owner"
        )
        return [], UISEONG_PARSER, meta
    if session_factory is None:
        if not allow_raw_requests_for_tests:
            meta["configured_collection_error"] = "managed session_factory injection is required"
            return [], UISEONG_PARSER, meta
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
        return [], UISEONG_PARSER, meta

    current_fetcher = fetcher or _request
    session = session_factory()
    try:
        first = _fetch_parsed_list(session, 1, int(timeout), current_fetcher, meta)
        if first["empty"]:
            raise UiseongContractError("canonical historical ledger unexpectedly empty")
        advertised_last = int(first["advertised_last"])
        if advertised_last > int(max_pages):
            meta["source_cap_reached"] = True
            raise UiseongContractError(
                f"advertised last page {advertised_last} exceeds max_pages {max_pages}"
            )
        pages = [first]
        for page_number in range(2, advertised_last + 1):
            parsed = _fetch_parsed_list(
                session, page_number, int(timeout), current_fetcher, meta
            )
            if parsed["empty"] or int(parsed["advertised_last"]) != advertised_last:
                raise UiseongContractError("data pagination boundary drift")
            pages.append(parsed)
        sentinel_page = advertised_last + 1
        sentinel = _fetch_parsed_list(
            session, sentinel_page, int(timeout), current_fetcher, meta
        )
        if not sentinel["empty"] or int(sentinel["advertised_last"]) != advertised_last:
            raise UiseongContractError("immediate post-boundary sentinel drift")
        for page in pages[:-1]:
            if len(page["rows"]) != UISEONG_PAGE_SIZE:
                raise UiseongContractError("non-final data page is not a full 20-row page")
        if not 1 <= len(pages[-1]["rows"]) <= UISEONG_PAGE_SIZE:
            raise UiseongContractError("final data page size drift")

        listed = [row for page in pages for row in page["rows"]]
        identities = [str(row["identity"]) for row in listed]
        numeric_ids = [int(identity) for identity in identities]
        if len(identities) != len(set(identities)):
            raise UiseongContractError("course identity repeated across pages")
        if any(left <= right for left, right in zip(numeric_ids, numeric_ids[1:])):
            raise UiseongContractError("course identities are not globally descending")
        source_by_id = {str(row["identity"]): row for row in listed}
        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "data_pages": len(pages),
                "nonempty_pages": len(pages),
                "page_counts": [len(page["rows"]) for page in pages],
                "advertised_last_page": advertised_last,
                "sentinel_page": sentinel_page,
                "sentinel_verified": True,
                "source_rows": len(listed),
                "source_total": len(listed),
                "source_capacity_shape_counts": dict(
                    Counter(str(row["capacity_shape"]) for row in listed)
                ),
                "source_reversed_date_anomaly_count": sum(
                    bool(row["reversed_event_range_anomaly"]) for row in listed
                ),
                "pagination_complete": True,
            }
        )

        branch_counts: dict[str, int] = {}
        branch_pages: dict[str, int] = {}
        for branch in UISEONG_BRANCHES:
            count, last, edges = _filtered_count(
                session,
                int(timeout),
                int(max_pages),
                current_fetcher,
                meta,
                branch_code=branch.code,
            )
            _validate_filter_edges(
                edges, source_by_id, branch_code=branch.code
            )
            branch_counts[branch.name] = count
            branch_pages[branch.name] = last
        source_branch_counts = Counter(str(row["branch"]) for row in listed)
        if branch_counts != {
            branch.name: source_branch_counts.get(branch.name, 0)
            for branch in UISEONG_BRANCHES
        }:
            raise UiseongContractError("official branch partition does not reconcile")

        status_counts: dict[str, int] = {}
        status_pages: dict[str, int] = {}
        for status_code, status_name in UISEONG_STATUS_FILTERS:
            count, last, edges = _filtered_count(
                session,
                int(timeout),
                int(max_pages),
                current_fetcher,
                meta,
                status_code=status_code,
            )
            _validate_filter_edges(
                edges, source_by_id, status_code=status_code
            )
            status_counts[status_name] = count
            status_pages[status_name] = last
        source_status_counts = Counter(str(row["raw_status"]) for row in listed)
        if status_counts != {
            status_name: source_status_counts.get(status_name, 0)
            for _, status_name in UISEONG_STATUS_FILTERS
        }:
            raise UiseongContractError("official status partition does not reconcile")
        meta.update(
            {
                "branch_filter_counts": branch_counts,
                "branch_filter_pages": branch_pages,
                "status_filter_counts": status_counts,
                "status_filter_pages": status_pages,
                "source_branch_counts": dict(source_branch_counts),
                "source_status_counts": dict(source_status_counts),
                "source_category_counts": dict(
                    Counter(str(row["category"]) for row in listed)
                ),
                "source_target_counts": dict(
                    Counter(str(row["target"]) for row in listed)
                ),
                "filter_census_complete": True,
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
            raise UiseongContractError(
                f"detail_limit {detail_limit} below required {len(current)}"
            )

        detail_results: list[tuple[dict[str, Any], int]] = []

        def fetch_detail(item: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
            detail_session = session_factory()
            try:
                soup, attempts = _fetch_soup(
                    detail_session,
                    str(item["detail_url"]),
                    int(timeout),
                    current_fetcher,
                )
                return _parse_detail(item, soup, cutoff), attempts
            finally:
                close_detail = getattr(detail_session, "close", None)
                if callable(close_detail):
                    close_detail()

        if current:
            with ThreadPoolExecutor(max_workers=min(int(max_workers), len(current))) as executor:
                futures = [executor.submit(fetch_detail, item) for item in current]
                for future in as_completed(futures):
                    detail_results.append(future.result())
                    meta["source_requests"] += 1
                    meta["detail_pages"] += 1
                    meta["request_attempts"] += detail_results[-1][1]
        rows = [row for row, _ in detail_results]

        first_recheck = _fetch_parsed_list(
            session, 1, int(timeout), current_fetcher, meta
        )
        meta["boundary_rechecks"] += 1
        if _page_signature(first_recheck) != _page_signature(first):
            raise UiseongContractError("page-one stability recheck failed")
        meta["page1_rechecked"] = True
        last_recheck = _fetch_parsed_list(
            session, advertised_last, int(timeout), current_fetcher, meta
        )
        meta["boundary_rechecks"] += 1
        if _page_signature(last_recheck) != _page_signature(pages[-1]):
            raise UiseongContractError("last-page stability recheck failed")
        meta["last_page_rechecked"] = True
        sentinel_recheck = _fetch_parsed_list(
            session, sentinel_page, int(timeout), current_fetcher, meta
        )
        meta["boundary_rechecks"] += 1
        if _page_signature(sentinel_recheck) != _page_signature(sentinel):
            raise UiseongContractError("sentinel stability recheck failed")
        meta["sentinel_rechecked"] = True

        rows.sort(key=lambda row: (row["start_date"], row["provider_course_id"]))
        rows = list((dedupe_rows or _dedupe)(rows))
        expected_ids = {
            f"{UISEONG_PROVIDER}:lctre:{item['identity']}" for item in current
        }
        if len(rows) != len(current) or {
            str(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise UiseongContractError("dedupe changed the current identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        meta["privacy_violations"] = len(privacy_errors)
        if privacy_errors:
            raise UiseongContractError("; ".join(privacy_errors[:5]))
        semantic_counts = Counter(_semantic_key(row) for row in rows)
        semantic_duplicates = sum(count - 1 for count in semantic_counts.values() if count > 1)
        meta["semantic_duplicate_count"] = semantic_duplicates
        if semantic_duplicates:
            raise UiseongContractError("semantic duplicate current courses detected")

        meta.update(
            {
                "returned_count": len(rows),
                "details_complete": meta["detail_pages"] == len(current),
                "status_counts": dict(Counter(str(row["status"]) for row in rows)),
                "raw_status_counts": dict(Counter(str(row["raw_status"]) for row in rows)),
                "branch_counts": dict(Counter(str(row["branch"]) for row in rows)),
                "category_counts": dict(Counter(str(row["category"]) for row in rows)),
                "target_counts": dict(Counter(str(row["target"]) for row in rows)),
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
        return rows, UISEONG_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], UISEONG_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


collect = collect_uiseong_education
