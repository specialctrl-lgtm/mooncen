"""Fail-closed collector for Yangyang-gun's official education catalogue.

The retained provider identity already points at Yangyang Lifelong Learning
Center, but its generic YAML collector stops at a configured row cap and can
persist arbitrary card text.  This dedicated collector instead proves the
complete regular catalogue through its all/day/night partitions and walks the
special-course catalogue independently.  Every catalogue gets an immediate
empty sentinel and a stable page-one recheck.

Course details are embedded in the list cards.  The source's application
button is validated per course; actionable controls are also checked against
the official unauthenticated real-name-login gate.  Instructor/contact data,
course-content HTML, images, notices, attachments, login data and application
payloads are never persisted.  Any pagination, partition, field, identity or
control drift invalidates the whole snapshot.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YANGYANG_PROVIDER = "MUNI_EDU_YANGYANG_GO_KR_06A9551C"
YANGYANG_CANONICAL_CANDIDATE_ID = "MUNI_IR_784530854A57"
YANGYANG_REGISTERED_CANDIDATE_ID = "MUNI_IR_A42915C11A17"
YANGYANG_MUNICIPALITY_CODE = "5183000000"
YANGYANG_MUNICIPALITY_NAME = "강원특별자치도 양양군"
YANGYANG_BRANCH = "양양군평생학습관"
YANGYANG_HOST = "edu.yangyang.go.kr"
YANGYANG_LIST_PATH = "/lecture/class_list.php"
YANGYANG_APPLICATION_PATH = "/lecture/reserve_write.php"
YANGYANG_AUTH_PATH = "/page/credit.php"
YANGYANG_CANONICAL_URL = f"https://{YANGYANG_HOST}{YANGYANG_LIST_PATH}"
YANGYANG_SPECIAL_URL = f"{YANGYANG_CANONICAL_URL}?lco_type=1"
YANGYANG_PAGE_SIZE = 15
YANGYANG_FETCH_ATTEMPTS = 2
YANGYANG_MAX_WORKERS = 12
YANGYANG_MAX_HTML_BYTES = 3_000_000
YANGYANG_TEST_IDENTITY = "473"
YANGYANG_TEST_TITLE = "테스트"
YANGYANG_PARSER = (
    "yangyang_official_regular_all_day_night_exact_union+special_all_pages+"
    "empty_sentinels+stable_page1+embedded_details+course_bound_application_"
    "controls+real_name_auth_gate+semantic_test_exclusion+pii_allowlist"
)
YANGYANG_OWNERSHIP_SCOPE = (
    "yangyang_official_lifelong_learning_regular_and_special_courses"
)

YANGYANG_REGISTERED_NOTICE_URL = (
    "https://edu.yangyang.go.kr/bbs/board.php?bo_table=notice&wr_id=138"
)
YANGYANG_DEPRECATED_HOME_URL = "https://edu.yangyang.go.kr/"
YANGYANG_COUNTY_PORTAL_URL = "https://www.yangyang.go.kr/gw/portal"
YANGYANG_EDUCATION_SUPPORT_BOARD_URL = (
    "https://gwsyed.gwe.go.kr/boardCnts/list.do?boardID=2871&m=030501"
)
YANGYANG_LIBRARY_MAIN_URL = "https://lib.gwe.go.kr/yylib/main"
YANGYANG_LIBRARY_PROGRAM_URL = (
    "https://lib.gwe.go.kr/yylib/menu/2614/lecture-event/list/all"
)

YANGYANG_EXCLUDED_CANDIDATE_IDS = frozenset(
    {
        "MUNI_IR_A47A9A294585",
        "MUNI_IR_A42915C11A17",
        "MUNI_IR_24A9044873D3",
        "MUNI_IR_91CEDCA0D277",
        "MUNI_IR_6727AF06D03B",
        "MUNI_IR_987E88A3B3B4",
    }
)
YANGYANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    YANGYANG_CANONICAL_CANDIDATE_ID: {
        "decision": "include_canonical_catalogue_with_retained_existing_owner",
        "provider": YANGYANG_PROVIDER,
        "url": YANGYANG_CANONICAL_URL,
        "owner": YANGYANG_PROVIDER,
        "reason": "official structured regular-course catalogue",
    },
    "MUNI_IR_7EB5E8E1192F": {
        "decision": "include_alias_under_same_owner",
        "provider": "MUNI_EDU_YANGYANG_GO_KR_05B51D79",
        "url": YANGYANG_SPECIAL_URL,
        "owner": YANGYANG_PROVIDER,
        "reason": "official special-course partition of the same catalogue",
    },
    "MUNI_IR_A47A9A294585": {
        "decision": "excluded_deprecated_duplicate_homepage",
        "provider": "MUNI_EDU_YANGYANG_GO_KR_8EB7CE85",
        "url": YANGYANG_DEPRECATED_HOME_URL,
        "owner": YANGYANG_PROVIDER,
        "reason": "deprecated duplicate homepage, not a list endpoint",
    },
    YANGYANG_REGISTERED_CANDIDATE_ID: {
        "decision": "excluded_notice_alias_but_retain_its_provider_identity",
        "provider": YANGYANG_PROVIDER,
        "url": YANGYANG_REGISTERED_NOTICE_URL,
        "owner": YANGYANG_PROVIDER,
        "reason": (
            "single external-course notice, not the canonical catalogue; "
            "the established provider identity is retained for continuity"
        ),
    },
    "MUNI_IR_24A9044873D3": {
        "decision": "excluded_general_county_portal_duplicate",
        "provider": "MUNI_WWW_YANGYANG_GO_KR_45EF0349",
        "url": YANGYANG_COUNTY_PORTAL_URL,
        "owner": YANGYANG_PROVIDER,
        "reason": "general municipal portal linking to the lifelong site",
    },
    "MUNI_IR_91CEDCA0D277": {
        "decision": "excluded_education_support_notice_board",
        "provider": "MUNI_GWSYED_GWE_GO_KR_C7CB43C2",
        "url": YANGYANG_EDUCATION_SUPPORT_BOARD_URL,
        "owner": "",
        "reason": "education-support public/hiring notices, not course records",
    },
    "MUNI_IR_6727AF06D03B": {
        "decision": "excluded_separate_education_library_owner",
        "provider": "MUNI_LIB_GWE_GO_KR_0A37071B",
        "url": YANGYANG_LIBRARY_MAIN_URL,
        "owner": "MUNI_LIB_GWE_GO_KR_CB6B94A3",
        "reason": "Gangwon education-library programme catalogue is a separate owner",
    },
    "MUNI_IR_987E88A3B3B4": {
        "decision": "excluded_from_municipal_owner_use_separate_library_catalogue",
        "provider": "MUNI_LIB_GWE_GO_KR_CB6B94A3",
        "url": YANGYANG_LIBRARY_PROGRAM_URL,
        "owner": "MUNI_LIB_GWE_GO_KR_CB6B94A3",
        "reason": "exact Yangyang education-library programme endpoint",
    },
}

YANGYANG_PROVIDER_AUDIT: Mapping[str, Mapping[str, str]] = {
    YANGYANG_PROVIDER: {
        "decision": "include_existing_owner_with_fail_closed_replacement",
        "url": YANGYANG_CANONICAL_URL,
        "reason": (
            "retain live provider identity while replacing the capped generic "
            "collector with complete partition validation"
        ),
    },
    "MUNI_EDU_YANGYANG_GO_KR_8EB7CE85": {
        "decision": "excluded_deprecated_duplicate",
        "url": YANGYANG_DEPRECATED_HOME_URL,
        "reason": f"deprecated duplicate of {YANGYANG_PROVIDER}",
    },
    "MUNI_WWW_YANGYANG_GO_KR_45EF0349": {
        "decision": "excluded_blocked_general_portal_duplicate",
        "url": YANGYANG_COUNTY_PORTAL_URL,
        "reason": f"general portal duplicate of {YANGYANG_PROVIDER}",
    },
    "MUNI_GWSYED_GWE_GO_KR_0D269FFD": {
        "decision": "excluded_non_course_notice_board",
        "url": YANGYANG_EDUCATION_SUPPORT_BOARD_URL,
        "reason": "legacy provider for an education-support notice board",
    },
    "MUNI_LIB_GWE_GO_KR_CB6B94A3": {
        "decision": "keep_separate_library_owner_not_currently_configured",
        "url": YANGYANG_LIBRARY_PROGRAM_URL,
        "reason": "official education-library catalogue had four live rows at audit",
    },
}

YANGYANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "coverage_candidate_count": 3,
    "coverage_owner_arrays_stale_empty": True,
    "existing_ready_provider": YANGYANG_PROVIDER,
    "existing_quality_collected": 10,
    "existing_quality_score": 90.9,
    "existing_quality_grade": "A",
    "existing_parser": "yangyang_lifelong_class_list",
    "existing_command_row_cap": 50,
    "existing_command_allows_partial_save": True,
    "regular_all_total": 51,
    "regular_all_page_counts": [15, 15, 15, 6],
    "regular_day_total": 32,
    "regular_day_page_counts": [15, 15, 2],
    "regular_night_total": 19,
    "regular_night_page_counts": [15, 4],
    "regular_partition_exact_union": True,
    "special_total": 0,
    "immediate_empty_sentinels": {
        "regular_all": 5,
        "regular_day": 4,
        "regular_night": 3,
        "special_all": 2,
    },
    "page_one_rechecks_stable": True,
    "source_identity_duplicates": 0,
    "current_or_future_rows": 51,
    "semantic_test_rows_excluded_after_validation": 1,
    "real_rows_returnable": 50,
    "source_status_counts": {"수강신청": 1, "신청대기중": 50},
    "course_bound_actionable_controls": 1,
    "real_name_auth_gates_verified": 1,
    "inactive_controls_with_wrong_source_identity": 14,
    "inactive_controls_with_empty_href": 36,
    "separate_library_live_rows": 4,
    "required_list_requests": 18,
    "conclusion": (
        "retain the existing lifelong-learning owner, replace its capped/partial "
        "collector, and keep the education library as a separate owner"
    ),
}

YANGYANG_PII_FIELDS_DISCARDED = (
    "강의내용",
    "강사명",
    "경력사항",
    "문의/연락처",
    "이미지 및 첨부파일",
    "공지 본문",
    "실명인증/로그인 정보",
    "신청 form payload",
    "source HTML",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"^([1-9]\d*)\.\s*(.+)$")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_TIMED_RANGE_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+(\d{1,2})시(?:\s*(\d{1,2})분)?"
    r"\s*~\s*(20\d{2}-\d{2}-\d{2})\s+(\d{1,2})시"
    r"(?:\s*(\d{1,2})분)?$"
)
_CAPACITY_RE = re.compile(r"^(\d{1,7})\s*/\s*(\d{1,7})$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_AUTH_MESSAGE = "실명인증으로 로그인 후 사용하십시오."
_NO_ROWS_MESSAGE = "등록된 강의가 없습니다."
_CARD_FIELDS = (
    "년도",
    "강의기간",
    "기수",
    "강의시간",
    "정원/신청인원",
    "접수기간",
    "수강료",
    "강의장소",
    "강의구분",
    "납부기간",
    "모집제한",
    "선발기준",
    "강의내용",
)
_SOURCE_STATUS_MAP: Mapping[str, str] = {
    "수강신청": "OPEN",
    "신청대기중": "SCHEDULED",
    "신청대기": "SCHEDULED",
    "신청마감": "CLOSED",
    "신청종료": "CLOSED",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
}
_PARTITION_QUERY: Mapping[str, tuple[str, str]] = {
    "regular_all": ("", ""),
    "regular_day": ("0", ""),
    "regular_night": ("1", ""),
    "special_all": ("", "1"),
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_number",
        "source_catalogue",
        "source_status",
        "source_year",
        "source_term",
        "source_period",
        "source_application_period",
        "source_schedule",
        "source_capacity_total",
        "source_capacity_current",
        "source_fee",
        "source_venue",
        "source_course_type",
        "source_target",
        "source_selection_method",
        "source_payment_period",
        "embedded_detail_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "real_name_auth_gate_verified",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "강의내용",
        "강사명",
        "경력사항",
        "instructor",
        "instructor_name",
        "contact",
        "contact_name",
        "phone",
        "email",
        "attachments",
        "attachment_urls",
        "detail_pairs",
        "source_html",
        "raw_html",
        "login_payload",
        "application_payload",
    }
)


class YangyangContractError(ValueError):
    """Raised when the official Yangyang source contract changes."""


@dataclass(frozen=True)
class _ListPage:
    rows: tuple[dict[str, Any], ...]
    displayed_page: Optional[int]
    advertised_last: int
    empty_marker: bool


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("today must be an ISO date") from exc


def _canonical_target_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    try:
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname != YANGYANG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != YANGYANG_LIST_PATH
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return YANGYANG_CANONICAL_URL


def is_yangyang_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YANGYANG_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
        == YANGYANG_CANONICAL_URL
    )


is_target = is_yangyang_target


def yangyang_list_url(partition: str, page: int = 1) -> str:
    if partition not in _PARTITION_QUERY:
        raise ValueError(f"unknown Yangyang partition {partition!r}")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    lc_type, lco_type = _PARTITION_QUERY[partition]
    pairs: list[tuple[str, str]] = []
    if lc_type:
        pairs.append(("lc_type", lc_type))
    if lco_type:
        pairs.append(("lco_type", lco_type))
    if page > 1:
        pairs.append(("page", str(page)))
    return YANGYANG_CANONICAL_URL + (f"?{urlencode(pairs)}" if pairs else "")


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": YANGYANG_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise YangyangContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise YangyangContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise YangyangContractError("empty HTTP response")
    if len(content) > YANGYANG_MAX_HTML_BYTES:
        raise YangyangContractError("HTTP response exceeded HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > YANGYANG_MAX_HTML_BYTES:
            raise YangyangContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > YANGYANG_MAX_HTML_BYTES:
            raise YangyangContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor response")
    if len(content) > YANGYANG_MAX_HTML_BYTES:
        raise YangyangContractError("HTTP response exceeded HTML byte cap")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    last_error: Optional[Exception] = None
    for _attempt in range(YANGYANG_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise YangyangContractError(f"{label} changed")
    return nodes[0]


def _official_url(value: Any, *, path: str) -> tuple[str, dict[str, str]]:
    absolute = urljoin(YANGYANG_CANONICAL_URL, _clean(value))
    parsed = urlparse(absolute)
    try:
        port = parsed.port
    except ValueError as exc:
        raise YangyangContractError("malformed official URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != YANGYANG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != path
        or parsed.params
        or parsed.fragment
    ):
        raise YangyangContractError(f"off-owner or unexpected path URL: {absolute}")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query: dict[str, str] = {}
    for key, value in pairs:
        if key in query and query[key] != value:
            raise YangyangContractError("conflicting duplicate official URL query parameter")
        query[key] = value
    normalized = f"https://{YANGYANG_HOST}{path}"
    if query:
        # The official template currently repeats lc_type on partitioned
        # application links.  Identical duplicates have one meaning, so accept
        # them at the boundary but emit one canonical parameter.
        normalized += "?" + urlencode(list(query.items()))
    return normalized, query


def _validate_list_link(value: Any, partition: str) -> int:
    _url, query = _official_url(value, path=YANGYANG_LIST_PATH)
    if not set(query) <= {"lc_type", "lco_type", "page"}:
        raise YangyangContractError("list link query fields changed")
    lc_type, lco_type = _PARTITION_QUERY[partition]
    if query.get("lc_type", "") != lc_type or query.get("lco_type", "") != lco_type:
        raise YangyangContractError(f"{partition} list link escaped its partition")
    page_text = query.get("page", "1")
    if not _IDENTITY_RE.fullmatch(page_text):
        raise YangyangContractError("list link page changed")
    return int(page_text)


def _validate_search_contract(soup: BeautifulSoup, partition: str) -> None:
    title = _one(soup.select("head > title"), f"{partition} document title")
    if _clean(title.get_text(" ", strip=True)) != "강의목록 | 양양군평생학습관":
        raise YangyangContractError(f"{partition} official title changed")
    heading = _one(soup.select("h1"), f"{partition} page heading")
    expected_heading = "특별강좌신청" if partition == "special_all" else "강좌신청"
    if _clean(heading.get_text(" ", strip=True)) != expected_heading:
        raise YangyangContractError(f"{partition} page heading changed")

    form = _one(soup.select("form#fsearch[name='fsearch']"), f"{partition} search form")
    if _clean(form.get("method")).lower() != "get" or _clean(form.get("action")):
        raise YangyangContractError(f"{partition} search form transport changed")
    lc_type, lco_type = _PARTITION_QUERY[partition]
    hidden_expected = {"lc_type": lc_type, "lco_type": lco_type}
    for name, expected in hidden_expected.items():
        node = _one(
            form.select(f"input[type='hidden'][name='{name}']"),
            f"{partition} {name} input",
        )
        if _clean(node.get("value")) != expected:
            raise YangyangContractError(f"{partition} {name} binding changed")
    select = _one(form.select("select#sfl[name='sfl']"), f"{partition} search selector")
    options = tuple(
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in select.select("option")
    )
    if options != (("lc_title", "강의명"), ("lc_lecweek", "강의요일")):
        raise YangyangContractError(f"{partition} search selector changed")
    keyword = _one(form.select("input[name='stx']"), f"{partition} search keyword")
    if not keyword.has_attr("required") or _clean(keyword.get("value")):
        raise YangyangContractError(f"{partition} search keyword state changed")

    tabs = soup.select("a.btn.btn-outline-primary[href]")
    if partition == "special_all":
        if tabs:
            raise YangyangContractError("special catalogue unexpectedly gained regular tabs")
        return
    expected_tabs = (("전체", "regular_all"), ("주간반", "regular_day"), ("야간반", "regular_night"))
    if len(tabs) != len(expected_tabs):
        raise YangyangContractError("regular catalogue tabs changed")
    active_count = 0
    for node, (label, tab_partition) in zip(tabs, expected_tabs):
        if _clean(node.get_text(" ", strip=True)) != label:
            raise YangyangContractError("regular catalogue tab labels changed")
        if _validate_list_link(node.get("href"), tab_partition) != 1:
            raise YangyangContractError("regular catalogue tab destinations changed")
        active = "active" in (node.get("class") or [])
        active_count += int(active)
        if active != (tab_partition == partition):
            raise YangyangContractError(f"{partition} active tab changed")
    if active_count != 1:
        raise YangyangContractError("regular catalogue active-tab count changed")


def _parse_date_range(value: str, label: str) -> tuple[str, str]:
    match = _DATE_RANGE_RE.fullmatch(value)
    if not match:
        raise YangyangContractError(f"{label} changed: {value!r}")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
    except ValueError as exc:
        raise YangyangContractError(f"{label} contains invalid date") from exc
    if end < start:
        raise YangyangContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _parse_timed_range(value: str, label: str) -> tuple[str, str]:
    match = _TIMED_RANGE_RE.fullmatch(value)
    if not match:
        raise YangyangContractError(f"{label} changed: {value!r}")
    try:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(4))
    except ValueError as exc:
        raise YangyangContractError(f"{label} contains invalid date") from exc
    start_hour = int(match.group(2))
    end_hour = int(match.group(5))
    start_minute = int(match.group(3) or 0)
    end_minute = int(match.group(6) or 0)
    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
        raise YangyangContractError(f"{label} contains invalid hour")
    if not (0 <= start_minute <= 59 and 0 <= end_minute <= 59):
        raise YangyangContractError(f"{label} contains invalid minute")
    if end < start:
        raise YangyangContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _card_fields(card: Any, identity: str) -> dict[str, str]:
    labels = card.select(".th_st")
    values: list[str] = []
    names: list[str] = []
    for node in labels:
        sibling = node.find_next_sibling()
        if sibling is None or "td_st" not in (sibling.get("class") or []):
            raise YangyangContractError(f"course {identity} field pairing changed")
        names.append(_clean(node.get_text(" ", strip=True)))
        values.append(_clean(sibling.get_text(" ", strip=True)))
    if tuple(names) != _CARD_FIELDS or len(values) != len(_CARD_FIELDS):
        raise YangyangContractError(
            f"course {identity} embedded-detail fields changed: {tuple(names)!r}"
        )
    return dict(zip(_CARD_FIELDS, values))


def _parse_application_control(
    node: Any,
    *,
    identity: str,
    partition: str,
) -> tuple[str, str, bool]:
    label = _clean(node.get_text(" ", strip=True))
    if label not in _SOURCE_STATUS_MAP:
        raise YangyangContractError(
            f"course {identity} unknown application state {label!r}"
        )
    status = _SOURCE_STATUS_MAP[label]
    classes = set(node.get("class") or [])
    href = _clean(node.get("href"))
    active = status == "OPEN"
    if active:
        if "btn-primary" not in classes or "disabled" in classes or not href:
            raise YangyangContractError(f"course {identity} active control changed")
    else:
        if "disabled" not in classes:
            raise YangyangContractError(f"course {identity} inactive control became operable")
        if status == "SCHEDULED" and "btn-warning" not in classes:
            raise YangyangContractError(f"course {identity} scheduled control changed")
    application_url = ""
    if href:
        application_url, query = _official_url(href, path=YANGYANG_APPLICATION_PATH)
        if not set(query) <= {"page", "lc_idx", "lc_type", "lco_type"}:
            raise YangyangContractError(f"course {identity} control query changed")
        linked_identity = query.get("lc_idx", "")
        if not _IDENTITY_RE.fullmatch(linked_identity):
            raise YangyangContractError(f"course {identity} control identity changed")
        lc_type, lco_type = _PARTITION_QUERY[partition]
        if query.get("lc_type", "") != lc_type or query.get("lco_type", "") != lco_type:
            raise YangyangContractError(f"course {identity} control escaped its partition")
        if "page" in query and not _IDENTITY_RE.fullmatch(query["page"]):
            raise YangyangContractError(f"course {identity} control page changed")
        # The live product currently emits an incorrect lc_idx on some disabled
        # controls.  It is safe only because the control is inert and its URL is
        # discarded.  Actionable controls must always bind to their own course.
        if active and linked_identity != identity:
            raise YangyangContractError(f"course {identity} active control identity differs")
    if active and not application_url:
        raise YangyangContractError(f"course {identity} application URL missing")
    return label, application_url if active else "", bool(href)


def _parse_card(card: Any, partition: str, source_page_url: str) -> dict[str, Any]:
    title_node = _one(card.select(":scope > .title"), "course title block")
    controls = title_node.select(":scope > a.btn")
    control = _one(controls, "course application control")
    direct_title = _clean(" ".join(str(node) for node in title_node.find_all(string=True, recursive=False)))
    title_match = _TITLE_RE.fullmatch(direct_title)
    if not title_match:
        raise YangyangContractError(f"course list title/number changed: {direct_title!r}")
    list_number = int(title_match.group(1))
    title = _clean(title_match.group(2))
    if not title:
        raise YangyangContractError("empty course title")

    collapses = card.select(":scope > div > .row.collapse[id]")
    if len(collapses) != 1:
        # The live card's bordered wrapper is a direct child; support fixtures
        # with the same semantic nesting without accepting multiple identities.
        collapses = card.select(".row.collapse[id]")
    collapse = _one(collapses, f"course {list_number} embedded detail")
    collapse_id = _clean(collapse.get("id"))
    if not collapse_id.startswith("list_"):
        raise YangyangContractError(f"course {list_number} collapse identity changed")
    identity = collapse_id.removeprefix("list_")
    if not _IDENTITY_RE.fullmatch(identity):
        raise YangyangContractError(f"course {list_number} identity malformed")
    more = _one(card.select("a.more[href]"), f"course {identity} detail toggle")
    if _clean(more.get("href")) != f"#list_{identity}":
        raise YangyangContractError(f"course {identity} detail toggle identity differs")
    if "더보기" not in _clean(more.get_text(" ", strip=True)):
        raise YangyangContractError(f"course {identity} detail toggle changed")

    values = _card_fields(card, identity)
    if not re.fullmatch(r"20\d{2}", values["년도"]):
        raise YangyangContractError(f"course {identity} year changed")
    start, end = _parse_date_range(values["강의기간"], f"course {identity} period")
    if values["년도"] != start[:4]:
        raise YangyangContractError(f"course {identity} year/period differ")
    apply_start, apply_end = _parse_timed_range(
        values["접수기간"], f"course {identity} application period"
    )
    capacity = _CAPACITY_RE.fullmatch(values["정원/신청인원"])
    if not capacity:
        raise YangyangContractError(f"course {identity} capacity changed")
    capacity_total = int(capacity.group(1))
    capacity_current = int(capacity.group(2))
    if not values["기수"] or not values["강의시간"] or not values["강의장소"]:
        raise YangyangContractError(f"course {identity} required detail became empty")
    if values["강의구분"] not in {"주간반", "야간반"}:
        raise YangyangContractError(f"course {identity} course type changed")
    if partition == "regular_day" and values["강의구분"] != "주간반":
        raise YangyangContractError(f"course {identity} escaped day partition")
    if partition == "regular_night" and values["강의구분"] != "야간반":
        raise YangyangContractError(f"course {identity} escaped night partition")

    source_status, application_url, source_href_present = _parse_application_control(
        control, identity=identity, partition=partition
    )
    return {
        "identity": identity,
        "list_number": list_number,
        "title": title,
        "year": values["년도"],
        "start": start,
        "end": end,
        "term": values["기수"],
        "schedule": values["강의시간"],
        "capacity_total": capacity_total,
        "capacity_current": capacity_current,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "application_period": values["접수기간"],
        "fee": values["수강료"],
        "venue": values["강의장소"],
        "course_type": values["강의구분"],
        "payment_period": values["납부기간"],
        "target": values["모집제한"],
        "selection": values["선발기준"],
        "source_status": source_status,
        "status": _SOURCE_STATUS_MAP[source_status],
        "application_url": application_url,
        "source_control_href_present": source_href_present,
        "source_catalogue": "special" if partition == "special_all" else "regular",
        "source_partition": partition,
        "source_page_url": source_page_url,
        # 강의내용 is deliberately discarded here, before any persisted row
        # exists.  Commented instructor/career markup is never parsed.
    }


def _parse_list_page(
    soup: BeautifulSoup,
    partition: str,
    requested_page: int,
) -> _ListPage:
    _validate_search_contract(soup, partition)
    rows = tuple(
        _parse_card(card, partition, yangyang_list_url(partition, requested_page))
        for card in soup.select(".req_list")
    )
    current_nodes = soup.select(".pg_current")
    if rows:
        current = _one(current_nodes, f"{partition} current pager")
        current_match = re.search(r"[1-9]\d*", _clean(current.get_text(" ", strip=True)))
        if not current_match or int(current_match.group(0)) != requested_page:
            raise YangyangContractError(f"{partition} displayed page changed")
        displayed_page: Optional[int] = requested_page
        if soup.select("h3"):
            raise YangyangContractError(f"{partition} data page also claims no rows")
    else:
        if current_nodes:
            raise YangyangContractError(f"{partition} empty page has a current pager")
        headings = [_clean(node.get_text(" ", strip=True)) for node in soup.select("h3")]
        if headings != [_NO_ROWS_MESSAGE]:
            raise YangyangContractError(f"{partition} empty sentinel marker changed")
        displayed_page = None

    advertised_pages = {1}
    if rows:
        advertised_pages.add(requested_page)
    for node in soup.select("a.pg_page[href]"):
        advertised_pages.add(_validate_list_link(node.get("href"), partition))
    advertised_last = max(advertised_pages)
    end_nodes = soup.select("a.pg_page.pg_end[href]")
    if end_nodes:
        end = _one(end_nodes, f"{partition} last-page pager")
        end_page = _validate_list_link(end.get("href"), partition)
        if end_page != advertised_last:
            raise YangyangContractError(f"{partition} last-page pager changed")
    if len(rows) > YANGYANG_PAGE_SIZE:
        raise YangyangContractError(f"{partition} page exceeded official page size")
    identities = [row["identity"] for row in rows]
    if len(identities) != len(set(identities)):
        raise YangyangContractError(f"{partition} page contains duplicate identities")
    numbers = [row["list_number"] for row in rows]
    if any(left - 1 != right for left, right in zip(numbers, numbers[1:])):
        raise YangyangContractError(f"{partition} page numbering is not descending")
    return _ListPage(rows, displayed_page, advertised_last, not rows)


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    keys = (
        "identity",
        "title",
        "year",
        "start",
        "end",
        "term",
        "schedule",
        "capacity_total",
        "capacity_current",
        "apply_start",
        "apply_end",
        "application_period",
        "fee",
        "venue",
        "course_type",
        "payment_period",
        "target",
        "selection",
        "source_status",
        "status",
    )
    return tuple(row.get(key) for key in keys)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


def _branch_code() -> str:
    digest = hashlib.sha1(YANGYANG_BRANCH.encode("utf-8")).hexdigest()[:12].upper()
    return f"{YANGYANG_PROVIDER}:CENTER:{digest}"[:100]


def _price(value: str) -> Optional[int]:
    cleaned = _clean(value)
    if cleaned in {"무료", "0원", "0"}:
        return 0
    if re.fullmatch(r"[\d,]+(?:원)?", cleaned):
        return int(cleaned.replace(",", "").removesuffix("원"))
    return None


def _contains_pii(value: Any) -> bool:
    text = _clean(value)
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text))


def _walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield key
            yield from _walk_values(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            yield from _walk_values(nested)
    else:
        yield value


def _validate_persisted_row(row: Mapping[str, Any]) -> None:
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        raise YangyangContractError("persisted raw-field allowlist changed")
    keys = {
        _clean(value).casefold()
        for value in _walk_values(row)
        if isinstance(value, str)
    }
    forbidden = {value.casefold() for value in _FORBIDDEN_PERSISTED_KEYS}
    if keys & forbidden:
        raise YangyangContractError("forbidden PII/detail key reached persisted row")
    for value in _walk_values(row):
        if isinstance(value, str) and _contains_pii(value):
            raise YangyangContractError("phone/email reached persisted allowlist")
    if row.get("description") != row.get("title"):
        raise YangyangContractError("description must contain title only")
    if not row.get("reservation_available") and row.get("application_url"):
        raise YangyangContractError("inactive application URL must not be persisted")


def _validate_auth_gate(soup: BeautifulSoup, identity: str) -> None:
    title = _one(soup.select("head > title"), f"course {identity} auth title")
    if _clean(title.get_text(" ", strip=True)) != "오류안내 페이지 | 양양군평생학습관":
        raise YangyangContractError(f"course {identity} auth page ownership changed")
    if soup.select("form"):
        raise YangyangContractError(f"course {identity} auth gate exposed a form")
    text = _clean(soup.get_text(" ", strip=True))
    scripts = "\n".join(node.get_text("\n", strip=True) for node in soup.select("script"))
    if _AUTH_MESSAGE not in text or _AUTH_MESSAGE not in scripts:
        raise YangyangContractError(f"course {identity} real-name gate changed")
    match = re.search(r'document\.location\.replace\(["\']([^"\']+)["\']\)', scripts)
    if not match:
        raise YangyangContractError(f"course {identity} auth redirect changed")
    redirect_url, query = _official_url(match.group(1), path=YANGYANG_AUTH_PATH)
    if not redirect_url or set(query) != {"reurl", "loc_type"}:
        raise YangyangContractError(f"course {identity} auth redirect query changed")
    if query["loc_type"] != "0":
        raise YangyangContractError(f"course {identity} auth location changed")
    _return_url, return_query = _official_url(
        query["reurl"],
        path=YANGYANG_LIST_PATH,
    )
    if return_query != {"lc_type": "0"}:
        raise YangyangContractError(f"course {identity} auth return target changed")


def _course_row(
    source: Mapping[str, Any],
    target: Any,
    *,
    auth_verified: bool,
) -> dict[str, Any]:
    identity = source["identity"]
    open_now = source["status"] == "OPEN"
    raw_url = f"{source['source_page_url']}#list_{identity}"
    row = {
        "provider": YANGYANG_PROVIDER,
        "provider_course_id": f"{YANGYANG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": source["title"],
        "branch": YANGYANG_BRANCH,
        "branch_code": _branch_code(),
        "period": f"{source['start']} ~ {source['end']}",
        "start_date": source["start"],
        "end_date": source["end"],
        "apply_period": source["application_period"],
        "apply_start_date": source["apply_start"],
        "apply_end_date": source["apply_end"],
        "status": source["status"],
        "category": "교육",
        "program_type": "평생학습 강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_cards",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": source["schedule"],
        "target": source["target"],
        "fee": source["fee"],
        "room": source["venue"],
        "venue": source["venue"],
        "venue_name": source["venue"],
        "description": source["title"],
        "price": _price(source["fee"]),
        "price_text": source["fee"],
        "capacity_total": source["capacity_total"],
        "capacity_current": source["capacity_current"],
        "capacity_remaining": max(
            0, source["capacity_total"] - source["capacity_current"]
        ),
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": open_now,
        "application_url": source["application_url"] if open_now else "",
        "application_type": "ONLINE_RESERVATION" if open_now else "",
        "raw_url": raw_url,
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "identity": identity,
            "list_number": source["list_number"],
            "source_catalogue": source["source_catalogue"],
            "source_status": source["source_status"],
            "source_year": source["year"],
            "source_term": source["term"],
            "source_period": f"{source['start']}~{source['end']}",
            "source_application_period": source["application_period"],
            "source_schedule": source["schedule"],
            "source_capacity_total": source["capacity_total"],
            "source_capacity_current": source["capacity_current"],
            "source_fee": source["fee"],
            "source_venue": source["venue"],
            "source_course_type": source["course_type"],
            "source_target": source["target"],
            "source_selection_method": source["selection"],
            "source_payment_period": source["payment_period"],
            "embedded_detail_verified": True,
            "application_control_present": True,
            "application_control_contract": (
                "course_bound_actionable" if open_now else "disabled_inert_url_discarded"
            ),
            "application_control_verified": True,
            "real_name_auth_gate_verified": auth_verified,
        },
    }
    _validate_persisted_row(row)
    return row


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "list_rechecks": 0,
        "sentinel_pages": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "application_gate_attempts": 0,
        "application_gate_pages": 0,
        "source_rows": 0,
        "current_count": 0,
        "semantic_excluded_count": 0,
        "returned_count": 0,
        "unique_id_count": 0,
        "duplicate_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "partition_union_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": YANGYANG_MUNICIPALITY_CODE,
        "municipality_name": YANGYANG_MUNICIPALITY_NAME,
        "canonical_candidate_id": YANGYANG_CANONICAL_CANDIDATE_ID,
        "retained_provider": YANGYANG_PROVIDER,
        "canonical_url": YANGYANG_CANONICAL_URL,
        "special_url": YANGYANG_SPECIAL_URL,
        "ownership_scope": YANGYANG_OWNERSHIP_SCOPE,
        "candidate_audit": {
            key: dict(value) for key, value in YANGYANG_CANDIDATE_AUDIT.items()
        },
        "provider_audit": {
            key: dict(value) for key, value in YANGYANG_PROVIDER_AUDIT.items()
        },
        "discovery_audit": dict(YANGYANG_DISCOVERY_AUDIT),
        "municipality_coverage": [YANGYANG_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(YANGYANG_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
        "tls_certificate_verification": True,
    }


def collect_yangyang_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 300,
    today: Optional[date | datetime | str] = None,
    max_workers: int = YANGYANG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Yangyang education snapshot."""

    meta = _base_meta()
    if not is_yangyang_target(target):
        meta["configured_collection_error"] = (
            "target does not match the retained Yangyang provider and canonical URL"
        )
        return [], YANGYANG_PARSER, meta
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or timeout < 1
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or max_pages < 1
        or isinstance(detail_limit, bool)
        or not isinstance(detail_limit, int)
        or detail_limit < 0
        or isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], YANGYANG_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], YANGYANG_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    workers = min(max_workers, YANGYANG_MAX_WORKERS)
    meta["network_concurrency"] = workers
    partitions = tuple(_PARTITION_QUERY)

    def fetch_list(partition: str, page: int) -> _ListPage:
        soup = _fetch_soup(
            yangyang_list_url(partition, page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, partition, page)

    first_pages: dict[str, _ListPage] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(partitions))) as pool:
        futures = {pool.submit(fetch_list, partition, 1): partition for partition in partitions}
        for future in as_completed(futures):
            partition = futures[future]
            try:
                first_pages[partition] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{partition} page 1: {type(exc).__name__}: {_clean(exc)}"
                )
    if errors or set(first_pages) != set(partitions):
        meta["request_count"] = meta["list_requests"]
        meta["configured_collection_error"] = "; ".join(
            errors or ["one or more first pages are missing"]
        )
        return [], YANGYANG_PARSER, meta

    totals: dict[str, int] = {}
    lasts: dict[str, int] = {}
    for partition, parsed in first_pages.items():
        if parsed.rows:
            total = parsed.rows[0]["list_number"]
            last = math.ceil(total / YANGYANG_PAGE_SIZE)
            if parsed.advertised_last != last:
                errors.append(
                    f"{partition}: advertised last page {parsed.advertised_last} != {last}"
                )
        else:
            total = 0
            last = 1
            if parsed.advertised_last != 1:
                errors.append(f"{partition}: empty source advertises extra pages")
        totals[partition] = total
        lasts[partition] = last

    required_list_requests = sum(last + 2 for last in lasts.values())
    meta.update(
        {
            "required_list_requests": required_list_requests,
            "declared_source_rows_by_partition": dict(totals),
            "declared_data_pages_by_partition": dict(lasts),
            "pagination_detected": any(last > 1 for last in lasts.values()),
            "sentinel_mode": "immediate_post_last_explicit_no_course_heading",
        }
    )
    if errors:
        meta["request_count"] = meta["list_requests"]
        meta["configured_collection_error"] = "; ".join(errors)
        return [], YANGYANG_PARSER, meta
    if required_list_requests > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "request_count": meta["list_requests"],
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of "
                    f"{required_list_requests} required list requests"
                ),
            }
        )
        return [], YANGYANG_PARSER, meta

    jobs: list[tuple[str, str, int]] = []
    for partition in partitions:
        jobs.extend(
            (partition, "data", page) for page in range(2, lasts[partition] + 1)
        )
        jobs.append((partition, "sentinel", lasts[partition] + 1))
        jobs.append((partition, "recheck", 1))
    parsed_jobs: dict[tuple[str, str, int], _ListPage] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_list, partition, page): (partition, role, page)
            for partition, role, page in jobs
        }
        for future in as_completed(futures):
            partition, role, page = futures[future]
            try:
                parsed_jobs[(partition, role, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{partition} {role} page {page}: "
                    f"{type(exc).__name__}: {_clean(exc)}"
                )

    by_partition: dict[str, list[dict[str, Any]]] = {}
    for partition in partitions:
        total = totals[partition]
        last = lasts[partition]
        all_rows: list[dict[str, Any]] = []
        for page in range(1, last + 1):
            parsed = first_pages[partition] if page == 1 else parsed_jobs.get(
                (partition, "data", page)
            )
            if parsed is None:
                errors.append(f"{partition} data page {page}: response missing")
                continue
            expected = min(
                YANGYANG_PAGE_SIZE,
                max(0, total - (page - 1) * YANGYANG_PAGE_SIZE),
            )
            if len(parsed.rows) != expected or parsed.empty_marker != (expected == 0):
                errors.append(
                    f"{partition} data page {page}: row count "
                    f"{len(parsed.rows)} != {expected}"
                )
            if parsed.advertised_last != last:
                errors.append(f"{partition} data page {page}: page boundary changed")
            expected_numbers = list(
                range(
                    total - (page - 1) * YANGYANG_PAGE_SIZE,
                    total - (page - 1) * YANGYANG_PAGE_SIZE - expected,
                    -1,
                )
            )
            if [row["list_number"] for row in parsed.rows] != expected_numbers:
                errors.append(f"{partition} data page {page}: numbering changed")
            all_rows.extend(parsed.rows)
        sentinel = parsed_jobs.get((partition, "sentinel", last + 1))
        if sentinel is None:
            errors.append(f"{partition}: immediate empty sentinel missing")
        elif (
            sentinel.rows
            or not sentinel.empty_marker
            or sentinel.displayed_page is not None
            or sentinel.advertised_last != last
        ):
            errors.append(f"{partition}: immediate post-last page is not empty/stable")
        else:
            meta["sentinel_pages"] += 1
        recheck = parsed_jobs.get((partition, "recheck", 1))
        first = first_pages[partition]
        if recheck is None:
            errors.append(f"{partition}: page-one recheck missing")
        elif (
            recheck.advertised_last != first.advertised_last
            or recheck.displayed_page != first.displayed_page
            or recheck.empty_marker != first.empty_marker
            or _page_signature(recheck.rows) != _page_signature(first.rows)
        ):
            errors.append(f"{partition}: page one changed during traversal")
        else:
            meta["list_rechecks"] += 1
        identities = [row["identity"] for row in all_rows]
        if len(identities) != len(set(identities)):
            errors.append(f"{partition}: duplicate source identities")
        if len(all_rows) != total:
            errors.append(f"{partition}: complete row count {len(all_rows)} != {total}")
        by_partition[partition] = all_rows

    regular_all = {row["identity"]: row for row in by_partition["regular_all"]}
    regular_day = {row["identity"]: row for row in by_partition["regular_day"]}
    regular_night = {row["identity"]: row for row in by_partition["regular_night"]}
    special_all = {row["identity"]: row for row in by_partition["special_all"]}
    day_keys = set(regular_day)
    night_keys = set(regular_night)
    regular_keys = set(regular_all)
    if day_keys & night_keys:
        errors.append("regular day/night partitions overlap")
    if day_keys | night_keys != regular_keys:
        errors.append("regular all is not the exact day/night union")
    for identity in sorted(regular_keys & (day_keys | night_keys)):
        partition_row = regular_day.get(identity) or regular_night.get(identity)
        if partition_row is None or _row_signature(regular_all[identity]) != _row_signature(
            partition_row
        ):
            errors.append(f"regular partition signature differs for course {identity}")
    if regular_keys & set(special_all):
        errors.append("regular and special catalogues share source identities")

    listed = list(by_partition["regular_all"]) + list(by_partition["special_all"])
    identities = [row["identity"] for row in listed]
    duplicate_count = len(identities) - len(set(identities))
    if duplicate_count:
        errors.append(f"combined catalogue has {duplicate_count} duplicate identities")
    current_listed = [
        row for row in listed if date.fromisoformat(row["end"]) >= cutoff
    ]
    meta.update(
        {
            "source_rows": len(listed),
            "current_count": len(current_listed),
            "unique_id_count": len(set(identities)),
            "duplicate_count": duplicate_count,
            "regular_all_count": len(regular_all),
            "regular_day_count": len(regular_day),
            "regular_night_count": len(regular_night),
            "special_all_count": len(special_all),
            "partition_union_complete": not errors,
            "detail_attempts": len(current_listed),
            "detail_pages": len(current_listed),
        }
    )
    if len(current_listed) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap {detail_limit} is below required {len(current_listed)}"
        )

    active_sources = [row for row in current_listed if row["status"] == "OPEN"]
    auth_verified: set[str] = set()
    if not errors and active_sources:
        meta["application_gate_attempts"] = len(active_sources)

        def fetch_auth(row: Mapping[str, Any]) -> str:
            soup = _fetch_soup(
                row["application_url"],
                timeout=timeout,
                fetcher=current_fetcher,
                session_factory=current_factory,
            )
            _validate_auth_gate(soup, row["identity"])
            return row["identity"]

        with ThreadPoolExecutor(max_workers=min(workers, len(active_sources))) as pool:
            futures = {pool.submit(fetch_auth, row): row for row in active_sources}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    auth_verified.add(future.result())
                    meta["application_gate_pages"] += 1
                except Exception as exc:
                    errors.append(
                        f"application gate {row['identity']}: "
                        f"{type(exc).__name__}: {_clean(exc)}"
                    )

    semantic_excluded = [
        row
        for row in current_listed
        if row["identity"] == YANGYANG_TEST_IDENTITY
        and _normalized(row["title"]) == _normalized(YANGYANG_TEST_TITLE)
    ]
    retained_sources = [row for row in current_listed if row not in semantic_excluded]
    meta["semantic_excluded_count"] = len(semantic_excluded)
    rows: list[dict[str, Any]] = []
    if not errors:
        try:
            rows = [
                _course_row(
                    source,
                    target,
                    auth_verified=source["identity"] in auth_verified,
                )
                for source in retained_sources
            ]
            deduped = list(current_dedupe(rows))
            if len(deduped) != len(rows):
                raise YangyangContractError(
                    f"dedupe changed complete count {len(rows)} to {len(deduped)}"
                )
            rows = deduped
        except Exception as exc:
            errors.append(f"persisted allowlist/dedupe: {type(exc).__name__}: {_clean(exc)}")

    details_complete = (
        not meta["source_cap_reached"]
        and meta["detail_attempts"] == len(current_listed)
        and meta["detail_pages"] == len(current_listed)
    )
    controls_complete = (
        meta["application_gate_attempts"] == len(active_sources)
        and meta["application_gate_pages"] == len(active_sources)
    )
    pagination_complete = (
        meta["list_requests"] == required_list_requests
        and meta["sentinel_pages"] == len(partitions)
        and meta["list_rechecks"] == len(partitions)
    )
    snapshot_complete = (
        not errors
        and pagination_complete
        and meta["partition_union_complete"]
        and details_complete
        and controls_complete
        and len(rows) == len(retained_sources)
    )
    if not snapshot_complete:
        rows = []
    meta.update(
        {
            "request_count": meta["list_requests"] + meta["application_gate_attempts"],
            "returned_count": len(rows),
            "status_counts": dict(Counter(row["status"] for row in rows)),
            "source_status_counts": dict(
                Counter(row["source_status"] for row in current_listed)
            ),
            "actionable_count": sum(
                bool(row.get("reservation_available")) for row in rows
            ),
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": snapshot_complete and not retained_sources,
            "no_current_reason": (
                "complete official regular/special catalogues have no current/future real courses"
                if snapshot_complete and not retained_sources
                else ""
            ),
        }
    )
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
    return rows, YANGYANG_PARSER, meta


collect_yangyang_target = collect_yangyang_education
collect = collect_yangyang_education


__all__ = [
    "YANGYANG_BRANCH",
    "YANGYANG_CANONICAL_CANDIDATE_ID",
    "YANGYANG_CANONICAL_URL",
    "YANGYANG_CANDIDATE_AUDIT",
    "YANGYANG_DISCOVERY_AUDIT",
    "YANGYANG_EXCLUDED_CANDIDATE_IDS",
    "YANGYANG_LIBRARY_PROGRAM_URL",
    "YANGYANG_MUNICIPALITY_CODE",
    "YANGYANG_MUNICIPALITY_NAME",
    "YANGYANG_PARSER",
    "YANGYANG_PROVIDER",
    "YANGYANG_PROVIDER_AUDIT",
    "YANGYANG_SPECIAL_URL",
    "YangyangContractError",
    "collect",
    "collect_yangyang_education",
    "collect_yangyang_target",
    "is_target",
    "is_yangyang_target",
    "yangyang_list_url",
]
