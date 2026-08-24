"""Fail-closed collector for Gangjin-gun's official education catalogues.

The configured municipal provider currently points at ``/school``.  That
landing page is not an owner: it republishes incomplete slices of two booking
catalogues and also mixes in an editorial education board.  The existing
provider instead owns the two disjoint lecture-module catalogues below:

* lifelong-learning course applications; and
* resident digital-education applications.

Every declared page of both catalogues is reconciled, followed by the
immediate empty page and stable first/last boundary rechecks.  Only
current/future details are opened.  A detail must expose an exact education
venue, and an online application is retained only when an identity-bound
``mode=write`` control is visible for that same course.  The write form is
never fetched because it is an applicant/PII boundary.

Instructor, contact, attachments and free-form education content are never
read from their detail cells and are never persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


GANGJIN_PROVIDER = "MUNI_WWW_GANGJIN_GO_KR_501E7E4B"
GANGJIN_CANDIDATE_ID = "MUNI_IR_D691946BC131"
GANGJIN_EXISTING_OWNER_AUDIT_ID = (
    "EXISTING_MUNI_WWW_GANGJIN_GO_KR_501E7E4B"
)
GANGJIN_LANDING_AUDIT_ID = "OFFICIAL_GANGJIN_EDUCATION_AGGREGATE_LANDING"
GANGJIN_DIGITAL_AUDIT_ID = "OFFICIAL_GANGJIN_DIGITAL_APPLICATION_CATALOGUE"
GANGJIN_EDITORIAL_AUDIT_ID = "OFFICIAL_GANGJIN_EDITORIAL_EDUCATION_BOARD"
GANGJIN_SCHEDULE_AUDIT_ID = "OFFICIAL_GANGJIN_LIFELONG_STATIC_SCHEDULE"
GANGJIN_DASAN_AUDIT_ID = "OFFICIAL_GANGJIN_STALE_DASAN_GUIDANCE_BOARD"
GANGJIN_COLLEGE_AUDIT_ID = "OFFICIAL_GANGJIN_COLLEGE_INFORMATION_SCHEDULES"

GANGJIN_HOST = "www.gangjin.go.kr"
GANGJIN_LANDING_PATH = "/school"
GANGJIN_LIFELONG_PATH = "/school/lifelong_study/lifelong_app"
GANGJIN_DIGITAL_PATH = "/school/informatization/info_app"
GANGJIN_EDITORIAL_PATH = "/school/edu/edu_list"
GANGJIN_SCHEDULE_PATH = "/school/lifelong_study/edu_schedule"
GANGJIN_DASAN_PATH = "/school/dasan/guidance"
GANGJIN_COLLEGE_PATH = "/school/county_college/schedule"
GANGJIN_LANDING_URL = f"https://{GANGJIN_HOST}{GANGJIN_LANDING_PATH}"
GANGJIN_LIFELONG_URL = f"https://{GANGJIN_HOST}{GANGJIN_LIFELONG_PATH}"
GANGJIN_DIGITAL_URL = f"https://{GANGJIN_HOST}{GANGJIN_DIGITAL_PATH}"
GANGJIN_CANONICAL_URL = GANGJIN_LIFELONG_URL
GANGJIN_EDITORIAL_URL = f"https://{GANGJIN_HOST}{GANGJIN_EDITORIAL_PATH}"
GANGJIN_SCHEDULE_URL = f"https://{GANGJIN_HOST}{GANGJIN_SCHEDULE_PATH}"
GANGJIN_DASAN_URL = f"https://{GANGJIN_HOST}{GANGJIN_DASAN_PATH}"
GANGJIN_COLLEGE_URL = f"https://{GANGJIN_HOST}{GANGJIN_COLLEGE_PATH}"
GANGJIN_MUNICIPALITY_CODE = "1278000000"
GANGJIN_MUNICIPALITY_NAME = "전남광주통합특별시 강진군"
GANGJIN_PAGE_SIZE = 15
GANGJIN_MAX_WORKERS = 8
GANGJIN_FETCH_ATTEMPTS = 3
GANGJIN_RETRY_BACKOFF_SECONDS = 0.2
GANGJIN_MAX_HTML_BYTES = 4_000_000
GANGJIN_PARSER = (
    "gangjin_official_lifelong+digital_all_pages+empty_sentinels+"
    "stable_boundaries+current_detail_venues+identity_bound_write_controls+"
    "current_semantic_duplicate_zero+pii_allowlist"
)
GANGJIN_OWNERSHIP_SCOPE = (
    "gangjin_official_lifelong_and_resident_digital_application_catalogues"
)

GANGJIN_SCOPE_PATHS: Mapping[str, str] = {
    "lifelong": GANGJIN_LIFELONG_PATH,
    "digital": GANGJIN_DIGITAL_PATH,
}
GANGJIN_SCOPE_TITLES: Mapping[str, str] = {
    "lifelong": "강좌신청 < 평생학습 - 강진군 교육정보",
    "digital": "교육신청 < 군민 디지털교육 - 강진군 교육정보",
}
GANGJIN_SEARCH_STATUSES: tuple[tuple[str, str], ...] = (
    ("all", "전체"),
    ("standby", "접수대기"),
    ("receipt", "접수중"),
    ("progress", "수강중"),
    ("end", "수강종료"),
    ("close", "폐강"),
)
GANGJIN_STATUS_MAP: Mapping[str, str] = {
    "접수하기": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "수강중": "CLOSED",
    "수강종료": "CLOSED",
    "폐강": "CANCELLED",
}

GANGJIN_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    GANGJIN_EXISTING_OWNER_AUDIT_ID: {
        "decision": "include_existing_provider_as_complete_two_scope_owner",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_LIFELONG_URL,
        "second_scope_url": GANGJIN_DIGITAL_URL,
        "owner": GANGJIN_PROVIDER,
    },
    GANGJIN_CANDIDATE_ID: {
        "decision": "replace_incomplete_aggregate_landing_with_canonical_catalogues",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_LANDING_URL,
        "owner": GANGJIN_PROVIDER,
    },
    GANGJIN_LANDING_AUDIT_ID: {
        "decision": "exclude_duplicate_incomplete_three_source_aggregate_landing",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_LANDING_URL,
        "owner": GANGJIN_PROVIDER,
    },
    GANGJIN_DIGITAL_AUDIT_ID: {
        "decision": "include_disjoint_booking_scope_under_existing_owner",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_DIGITAL_URL,
        "owner": GANGJIN_PROVIDER,
    },
    GANGJIN_EDITORIAL_AUDIT_ID: {
        "decision": "exclude_editorial_course_notice_board_without_structured_booking_branch",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_EDITORIAL_URL,
        "owner": "gangjin_editorial_education_information_board",
    },
    GANGJIN_SCHEDULE_AUDIT_ID: {
        "decision": "exclude_static_schedule_republishing_lifelong_course_information",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_SCHEDULE_URL,
        "owner": GANGJIN_PROVIDER,
    },
    GANGJIN_DASAN_AUDIT_ID: {
        "decision": "exclude_stale_guidance_board_without_current_booking_contract",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_DASAN_URL,
        "owner": "gangjin_dasan_information_board",
        "latest_visible_course_date": "2019-08-14",
    },
    GANGJIN_COLLEGE_AUDIT_ID: {
        "decision": "exclude_information_schedules_without_application_controls",
        "provider": GANGJIN_PROVIDER,
        "url": GANGJIN_COLLEGE_URL,
        "owner": "gangjin_resident_college_information_schedules",
    },
}

GANGJIN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": GANGJIN_CANONICAL_URL,
    "scope_urls": {
        "lifelong": GANGJIN_LIFELONG_URL,
        "digital": GANGJIN_DIGITAL_URL,
    },
    "source_rows": 215,
    "source_rows_by_scope": {"lifelong": 127, "digital": 88},
    "data_pages": 15,
    "data_pages_by_scope": {"lifelong": 9, "digital": 6},
    "required_list_requests": 21,
    "current_or_future_rows": 41,
    "current_scope_counts": {"lifelong": 41, "digital": 0},
    "expired_rows": 174,
    "current_source_status_counts": {"접수하기": 33, "접수마감": 8},
    "normalized_status_counts": {"OPEN": 33, "CLOSED": 8},
    "detail_pages_verified": 41,
    "visible_identity_bound_application_controls": 33,
    "current_venue_counts": {
        "강진군 평생학습센터": 28,
        "강진군평생학습센터": 8,
        "성진목공예(다산로 426-4": 2,
        "청소년수련관": 1,
        "강진군복지타운 3층": 1,
        "강진군 평생학습센터 요리실": 1,
    },
    "landing_total_rows": 254,
    "landing_editorial_rows": 57,
    "landing_lifelong_overlap": 110,
    "landing_lifelong_omissions": 17,
    "landing_digital_overlap": 87,
    "landing_digital_omissions": 1,
    "editorial_board_rows": 57,
    "editorial_current_or_future_rows": 1,
    "editorial_current_structured_application_controls": 0,
    "editorial_current_structured_venues": 0,
    "identity_duplicate_count": 0,
    "historical_reversed_application_period_count": 1,
    "conclusion": "two_disjoint_booking_catalogues_roll_up_to_existing_provider",
}

GANGJIN_PII_FIELDS_DISCARDED = (
    "강사명",
    "문의전화",
    "교육내용",
    "첨부파일",
    "application_form_values",
    "applicant_identity",
    "member_profile",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class GangjinContractError(ValueError):
    """Raised when the verified official catalogue contract changes."""


@dataclass(frozen=True)
class _ListPage:
    scope: str
    requested_page: int
    total: int
    source_pages: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_CSRF_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_VALUE_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?$")
_CAPTION_RE = re.compile(
    r"^(?P<pages>[\d,]+)페이지 중 (?P<page>[\d,]+)페이지, 전체 "
    r"(?P<total>[\d,]+)건 입니다\. 본 데이터표는 6컬럼, "
    r"(?P<rows>[\d,]+)로우로 구성되어 있습니다\. 각 로우는 번호, "
    r"강좌명, 정원, 신청기간, 상태, 접수로 이루어져 있습니다\.$"
)
_CAPACITY_RE = re.compile(
    r"^(?P<current>[\d,]+)\s*\(\s*(?P<wait>[\d,]+)\s*\)\s*/\s*"
    r"(?P<total>[\d,]+)\s*명$"
)
_FEE_RE = re.compile(r"^(?P<amount>[\d,]+)\s*원$")
_COUNT_RE = re.compile(r"^(?P<count>[\d,]+)\s*명$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIST_HEADERS: Mapping[str, tuple[str, ...]] = {
    "lifelong": ("강좌명", "신청자(대기자)/정원", "상태", "접수"),
    "digital": ("강좌명", "정원", "상태", "접수"),
}
_DETAIL_LABELS = (
    "강좌명",
    "강사명",
    "재료비",
    "교육대상",
    "신청기간",
    "교육기간",
    "교육장소",
    "교육내용",
    "모집정원",
    "대기정원",
)
_OPENING_STATES = frozenset({"개강", "수강종료", "폐강"})
_RECEIPT_CLASSES: Mapping[str, tuple[str, ...]] = {
    "접수하기": ("state", "state_receipt"),
    "접수대기": ("state", "state_waiting"),
    "접수마감": ("state", "state_finish"),
    "수강중": ("state", "state_finish"),
    "수강종료": ("state", "state_finish"),
    "폐강": ("state", "state_close"),
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "scope",
        "identity",
        "source_page",
        "source_position",
        "source_opening_state",
        "source_status",
        "source_period",
        "source_apply_period",
        "source_capacity_current",
        "source_wait_current",
        "source_capacity_total",
        "source_generic_login_indicator_present",
        "detail_fee",
        "detail_target",
        "detail_venue",
        "detail_capacity_total",
        "detail_capacity_wait_total",
        "detail_verified",
        "visible_application_control_present",
        "application_control_contract",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "contact",
        "phone",
        "email",
        "instructor",
        "lecturer",
        "education_content",
        "attachments",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_gangjin_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != GANGJIN_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == GANGJIN_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == GANGJIN_LIFELONG_PATH
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_gangjin_education_target


def is_gangjin_candidate_alias(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    url = _clean(_target_value(target, "url"))
    return bool(
        candidate == GANGJIN_CANDIDATE_ID
        or url
        in {
            GANGJIN_LANDING_URL,
            GANGJIN_DIGITAL_URL,
            GANGJIN_EDITORIAL_URL,
            GANGJIN_SCHEDULE_URL,
            GANGJIN_DASAN_URL,
            GANGJIN_COLLEGE_URL,
        }
    )


def gangjin_list_url(scope: str, page: int) -> str:
    key = _clean(scope)
    if key not in GANGJIN_SCOPE_PATHS:
        raise ValueError("unknown Gangjin catalogue scope")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"https://{GANGJIN_HOST}{GANGJIN_SCOPE_PATHS[key]}?" + urlencode(
        (("page", str(page)),)
    )


def gangjin_detail_url(scope: str, identity: Any) -> str:
    key = _clean(scope)
    value = _clean(identity)
    if key not in GANGJIN_SCOPE_PATHS:
        raise ValueError("unknown Gangjin catalogue scope")
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return f"https://{GANGJIN_HOST}{GANGJIN_SCOPE_PATHS[key]}?" + urlencode(
        (("idx", value), ("mode", "view"))
    )


def gangjin_application_url(scope: str, identity: Any) -> str:
    key = _clean(scope)
    value = _clean(identity)
    if key not in GANGJIN_SCOPE_PATHS:
        raise ValueError("unknown Gangjin catalogue scope")
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return f"https://{GANGJIN_HOST}{GANGJIN_SCOPE_PATHS[key]}?" + urlencode(
        (("idx", value), ("mode", "write"))
    )


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        parsed = _clean(value)
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", parsed) is None:
            raise ValueError("today must be YYYY-MM-DD")
        return date.fromisoformat(parsed)
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(GANGJIN_FETCH_ATTEMPTS):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            final = urlparse(_clean(getattr(response, "url", url)))
            if (
                final.scheme != "https"
                or (final.hostname or "").rstrip(".").lower() != GANGJIN_HOST
                or _safe_port(final) is not None
            ):
                raise GangjinContractError("official response redirected out of ownership")
            content = bytes(getattr(response, "content", b""))
            if len(content) > GANGJIN_MAX_HTML_BYTES:
                raise GangjinContractError("official HTML exceeded size cap")
            content_type = _clean(getattr(response, "headers", {}).get("Content-Type"))
            if content_type and not any(
                token in content_type.lower() for token in ("html", "xhtml", "text/plain")
            ):
                raise GangjinContractError("official response is not HTML")
            response.encoding = "utf-8"
            return response
        except Exception as exc:  # requests and contract errors share retry policy
            last_error = exc
            if attempt + 1 < GANGJIN_FETCH_ATTEMPTS:
                time.sleep(GANGJIN_RETRY_BACKOFF_SECONDS * (attempt + 1))
    assert last_error is not None
    raise last_error


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value.decode("utf-8"), "html.parser")
    if isinstance(value, str):
        return BeautifulSoup(value, "html.parser")
    content = getattr(value, "content", None)
    if isinstance(content, bytes):
        return BeautifulSoup(content.decode("utf-8"), "html.parser")
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return BeautifulSoup(text, "html.parser")
    raise TypeError("fetcher must return HTML, BeautifulSoup, or a response")


def _close_quietly(value: Any) -> None:
    closer = getattr(value, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _fetch_soup(
    url: str,
    *,
    timeout: int,
    fetcher: Fetcher,
    session_factory: SessionFactory,
) -> BeautifulSoup:
    session = session_factory()
    try:
        return _coerce_soup(fetcher(session, url, timeout))
    finally:
        _close_quietly(session)


def _owned_url(value: Any, scope: str, modes: frozenset[str]) -> tuple[str, str]:
    scope_url = f"https://{GANGJIN_HOST}{GANGJIN_SCOPE_PATHS[scope]}"
    parsed = urlparse(urljoin(scope_url, _clean(value)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != GANGJIN_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != GANGJIN_SCOPE_PATHS[scope]
        or parsed.fragment
    ):
        raise GangjinContractError("course URL escaped its official scope")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"idx", "mode"} or any(len(values) != 1 for values in query.values()):
        raise GangjinContractError("course URL query changed")
    identity = _clean(query["idx"][0])
    mode = _clean(query["mode"][0])
    if _POSITIVE_ID_RE.fullmatch(identity) is None or mode not in modes:
        raise GangjinContractError("course URL identity/mode changed")
    canonical = (
        gangjin_detail_url(scope, identity)
        if mode == "view"
        else gangjin_application_url(scope, identity)
    )
    return identity, canonical


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in node.find_all("option", recursive=False)
    )


def _validate_list_form(soup: BeautifulSoup, scope: str, page: int) -> None:
    forms = soup.select("form#list_search")
    if len(forms) != 1:
        raise GangjinContractError(f"{scope} page {page}: search form changed")
    form = forms[0]
    action = urlparse(urljoin(GANGJIN_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() not in {"", "get"}
        or action.scheme != "https"
        or (action.hostname or "").rstrip(".").lower() != GANGJIN_HOST
        or action.path != GANGJIN_SCOPE_PATHS[scope]
        or action.query
        or action.fragment
    ):
        raise GangjinContractError(f"{scope} page {page}: search ownership changed")
    csrf = form.select('input[type="hidden"][name="csrf_token"]')
    statuses = form.select('select#search_status[name="search_status"]')
    words = form.select('input#search_word[name="search_word"][type="text"]')
    starts = form.select('input#search_startdate[name="search_startdate"][type="text"]')
    ends = form.select('input#search_enddate[name="search_enddate"][type="text"]')
    submits = form.select('input[type="submit"]')
    if (
        len(csrf) != 1
        or _CSRF_RE.fullmatch(_clean(csrf[0].get("value"))) is None
        or len(statuses) != 1
        or _options(statuses[0]) != GANGJIN_SEARCH_STATUSES
        or len(statuses[0].select('option[value="all"][selected]')) != 1
        or len(words) != 1
        or _clean(words[0].get("value"))
        or len(starts) != 1
        or _clean(starts[0].get("value"))
        or len(ends) != 1
        or _clean(ends[0].get("value"))
        or len(submits) != 1
        or _clean(submits[0].get("value")) != "검색"
    ):
        raise GangjinContractError(f"{scope} page {page}: search controls changed")


def _parse_range(value: Any, prefix: str, identity: str) -> tuple[str, str, str, date, date]:
    text = _clean(value)
    marker = f"{prefix} - "
    if not text.startswith(marker):
        raise GangjinContractError(f"course {identity}: {prefix} label changed")
    values = text[len(marker) :].split(" ~ ")
    if len(values) != 2 or any(_DATE_VALUE_RE.fullmatch(item) is None for item in values):
        raise GangjinContractError(f"course {identity}: {prefix} range changed")
    if (len(values[0]) > 10) != (len(values[1]) > 10):
        raise GangjinContractError(f"course {identity}: {prefix} precision changed")
    start_date = date.fromisoformat(values[0][:10])
    end_date = date.fromisoformat(values[1][:10])
    return values[0], values[1], f"{values[0]} ~ {values[1]}", start_date, end_date


def _parse_capacity(cell: Any, identity: str) -> tuple[int, int, int]:
    text = _clean(cell.get_text(" ", strip=True))
    match = _CAPACITY_RE.fullmatch(text)
    blues = cell.select(":scope > span.blue_font")
    if match is None or len(blues) != 1:
        raise GangjinContractError(f"course {identity}: capacity changed")
    values = tuple(int(match.group(name).replace(",", "")) for name in ("current", "wait", "total"))
    if int(_clean(blues[0].get_text()).replace(",", "")) != values[0]:
        raise GangjinContractError(f"course {identity}: capacity emphasis changed")
    return values


def _parse_receipt(cell: Any, scope: str, identity: str, opening: str) -> tuple[str, bool]:
    text = _clean(cell.get_text(" ", strip=True))
    if text not in GANGJIN_STATUS_MAP:
        raise GangjinContractError(f"course {identity}: source status changed")
    spans = cell.select(":scope > span.state, :scope > a > span.state")
    if len(spans) != 1 or tuple(spans[0].get("class") or ()) != _RECEIPT_CLASSES[text]:
        raise GangjinContractError(f"course {identity}: source status class changed")
    links = cell.find_all("a", recursive=False)
    generic_login = text == "접수하기"
    if generic_login:
        if len(links) != 1:
            raise GangjinContractError(f"course {identity}: login indicator changed")
        parsed = urlparse(urljoin(GANGJIN_CANONICAL_URL, _clean(links[0].get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").rstrip(".").lower() != GANGJIN_HOST
            or parsed.path != "/www/operation_guide/member_login"
            or parsed.fragment
            or query != {"return_url": [GANGJIN_SCOPE_PATHS[scope]]}
        ):
            raise GangjinContractError(f"course {identity}: generic login route changed")
    elif links:
        raise GangjinContractError(f"course {identity}: inactive list exposes a link")
    status = "CANCELLED" if opening == "폐강" else GANGJIN_STATUS_MAP[text]
    if (opening == "폐강") != (text == "폐강"):
        raise GangjinContractError(f"course {identity}: opening/status mismatch")
    return status, generic_login


def _parse_list_row(row: Any, scope: str, page: int, position: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 4:
        raise GangjinContractError(f"{scope} page {page}: course columns changed")
    if "align_left" not in tuple(cells[0].get("class") or ()):
        raise GangjinContractError(f"{scope} page {page}: title cell changed")
    links = cells[0].find_all("a", recursive=False)
    if len(links) != 1:
        raise GangjinContractError(f"{scope} page {page}: detail link changed")
    identity, raw_url = _owned_url(links[0].get("href"), scope, frozenset({"view"}))
    titles = links[0].select(":scope > span.title")
    date_nodes = links[0].select(":scope > p.date")
    if len(titles) != 1 or len(date_nodes) != 1:
        raise GangjinContractError(f"course {identity}: list identity changed")
    title = _clean(titles[0].get_text(" ", strip=True))
    ranges = date_nodes[0].find_all("span", recursive=False)
    if not title or len(title) > 300 or len(ranges) != 2:
        raise GangjinContractError(f"course {identity}: list safe fields changed")
    apply_start, apply_end, apply_period, apply_start_date, apply_end_date = _parse_range(
        ranges[0].get_text(" ", strip=True), "신청기간", identity
    )
    start_text, end_text, period, start, end = _parse_range(
        ranges[1].get_text(" ", strip=True), "교육기간", identity
    )
    current, wait, total = _parse_capacity(cells[1], identity)
    opening = _clean(cells[2].get_text(" ", strip=True))
    if opening not in _OPENING_STATES:
        raise GangjinContractError(f"course {identity}: opening state changed")
    status, generic_login = _parse_receipt(cells[3], scope, identity, opening)
    return {
        "scope": scope,
        "identity": identity,
        "source_page": page,
        "source_position": position,
        "title": title,
        "raw_url": raw_url,
        "source_opening_state": opening,
        "source_status": _clean(cells[3].get_text(" ", strip=True)),
        "status": status,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "apply_period": apply_period,
        "apply_start_date": apply_start_date,
        "apply_end_date": apply_end_date,
        "start_text": start_text,
        "end_text": end_text,
        "period": period,
        "start": start,
        "end": end,
        "capacity_current": current,
        "wait_current": wait,
        "capacity_total": total,
        "generic_login_indicator": generic_login,
    }


def _validate_pagination(
    soup: BeautifulSoup,
    *,
    scope: str,
    requested_page: int,
    source_pages: int,
    sentinel: bool,
) -> None:
    roots = soup.select("div.paging > div.num")
    if len(roots) != 1:
        raise GangjinContractError(f"{scope} page {requested_page}: pagination changed")
    anchors = roots[0].find_all("a", recursive=False)
    if len(anchors) != source_pages:
        raise GangjinContractError(f"{scope} page {requested_page}: page boundary changed")
    for expected, anchor in enumerate(anchors, 1):
        if _clean(anchor.get_text(" ", strip=True)) != str(expected):
            raise GangjinContractError(f"{scope} page {requested_page}: page label changed")
        is_current = not sentinel and expected == requested_page
        if is_current:
            if tuple(anchor.get("class") or ()) != ("on",) or anchor.has_attr("href") or anchor.has_attr("title"):
                raise GangjinContractError(f"{scope} page {requested_page}: current page marker changed")
            continue
        parsed = urlparse(urljoin(GANGJIN_CANONICAL_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            anchor.get("class")
            or _clean(anchor.get("title")) != f"{expected} 페이지"
            or parsed.scheme != "https"
            or (parsed.hostname or "").rstrip(".").lower() != GANGJIN_HOST
            or parsed.path != GANGJIN_SCOPE_PATHS[scope]
            or parsed.fragment
            or query != {"page": [str(expected)]}
        ):
            raise GangjinContractError(f"{scope} page {requested_page}: page link changed")


def _parse_list_page(
    soup: BeautifulSoup,
    scope: str,
    page: int,
    *,
    expected_total: Optional[int] = None,
    expected_pages: Optional[int] = None,
    sentinel: bool = False,
) -> _ListPage:
    titles = soup.select("head > title")
    expected_title = f"{page} 페이지 목록보기 < {GANGJIN_SCOPE_TITLES[scope]}"
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != expected_title:
        raise GangjinContractError(f"{scope} page {page}: document title changed")
    _validate_list_form(soup, scope, page)
    tables = soup.select("table#lecture_new_table")
    if len(tables) != 1:
        raise GangjinContractError(f"{scope} page {page}: primary table changed")
    table = tables[0]
    headers = tuple(_clean(node.get_text(" ", strip=True)) for node in table.select(":scope > thead > tr > th"))
    if headers != _LIST_HEADERS[scope]:
        raise GangjinContractError(f"{scope} page {page}: table headers changed")
    captions = table.select(":scope > caption")
    if len(captions) != 1:
        raise GangjinContractError(f"{scope} page {page}: caption changed")
    match = _CAPTION_RE.fullmatch(_clean(captions[0].get_text(" ", strip=True)))
    if match is None:
        raise GangjinContractError(f"{scope} page {page}: caption contract changed")
    values = {key: int(match.group(key).replace(",", "")) for key in ("pages", "page", "total", "rows")}
    source_pages = max(1, math.ceil(values["total"] / GANGJIN_PAGE_SIZE))
    if (
        values["page"] != page
        or values["pages"] != source_pages
        or (expected_total is not None and values["total"] != expected_total)
        or (expected_pages is not None and source_pages != expected_pages)
    ):
        raise GangjinContractError(f"{scope} page {page}: declared boundary changed")
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise GangjinContractError(f"{scope} page {page}: table body changed")
    source_rows = bodies[0].find_all("tr", recursive=False)
    data_rows = [row for row in source_rows if row.select_one('td.align_left > a[href*="idx="]')]
    if data_rows:
        if len(data_rows) != len(source_rows):
            raise GangjinContractError(f"{scope} page {page}: mixed data/empty rows")
        rows = tuple(
            _parse_list_row(row, scope, page, (page - 1) * GANGJIN_PAGE_SIZE + offset)
            for offset, row in enumerate(data_rows, 1)
        )
        empty_marker = False
    else:
        if (
            len(source_rows) != 1
            or len(source_rows[0].find_all("td", recursive=False)) != 1
            or _clean(source_rows[0].find("td", recursive=False).get("colspan")) != "4"
            or _clean(source_rows[0].get_text(" ", strip=True)) != "개설된 강좌가 없습니다."
        ):
            raise GangjinContractError(f"{scope} page {page}: explicit empty marker changed")
        rows = ()
        empty_marker = True
    expected_count = 0 if sentinel else min(
        GANGJIN_PAGE_SIZE,
        max(0, values["total"] - (page - 1) * GANGJIN_PAGE_SIZE),
    )
    if len(rows) != expected_count or values["rows"] != len(rows):
        raise GangjinContractError(f"{scope} page {page}: page row count changed")
    _validate_pagination(
        soup,
        scope=scope,
        requested_page=page,
        source_pages=source_pages,
        sentinel=sentinel,
    )
    return _ListPage(scope, page, values["total"], source_pages, rows, empty_marker)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("scope")),
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("capacity_total")),
        )
        for row in rows
    )


def _safe_detail_text(node: Any, identity: str, label: str) -> str:
    value = _clean(node.get_text(" ", strip=True))
    if not value or len(value) > 300 or _PHONE_RE.search(value) or _EMAIL_RE.search(value):
        raise GangjinContractError(f"course {identity}: safe {label} changed")
    return value


def _fee(value: str, identity: str) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return "무료", 0
    match = _FEE_RE.fullmatch(text)
    if match is None:
        raise GangjinContractError(f"course {identity}: material fee changed")
    amount = int(match.group("amount").replace(",", ""))
    return f"{amount:,}원", amount


def _detail_count(value: str, identity: str, label: str) -> int:
    match = _COUNT_RE.fullmatch(_clean(value))
    if match is None:
        raise GangjinContractError(f"course {identity}: {label} changed")
    return int(match.group("count").replace(",", ""))


def _branch_name(venue: str) -> str:
    return f"{GANGJIN_MUNICIPALITY_NAME} / {venue}"


def _branch_code(venue: str) -> str:
    digest = hashlib.sha1(_clean(venue).encode("utf-8")).hexdigest()[:12]
    return f"gangjin:{digest}"


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    scope = _clean(listed.get("scope"))
    identity = _clean(listed.get("identity"))
    titles = soup.select("head > title")
    expected_title = f"{_clean(listed.get('title'))} < {GANGJIN_SCOPE_TITLES[scope]}"
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != expected_title:
        raise GangjinContractError(f"course {identity}: official detail title changed")
    roots = soup.select("#content > #board_basic_view")
    tables = soup.select("#content > #board_basic_view > table#lecture_view_table")
    if len(roots) != 1 or len(tables) != 1:
        raise GangjinContractError(f"course {identity}: primary detail changed")
    rows = tables[0].find_all("tr", recursive=False)
    labels: list[str] = []
    cells: list[Any] = []
    for row in rows:
        th = row.find_all("th", recursive=False)
        td = row.find_all("td", recursive=False)
        if len(th) != 1 or len(td) != 1:
            raise GangjinContractError(f"course {identity}: detail schema changed")
        labels.append(_clean(th[0].get_text(" ", strip=True)))
        cells.append(td[0])
    if tuple(labels) != _DETAIL_LABELS:
        raise GangjinContractError(f"course {identity}: detail labels changed")
    # Deliberately do not read cells[1] (instructor) or cells[7] (free-form content).
    title = _safe_detail_text(cells[0], identity, "title")
    fee_text, fee_amount = _fee(_safe_detail_text(cells[2], identity, "fee"), identity)
    target = _safe_detail_text(cells[3], identity, "target")
    apply_start, apply_end, apply_period, _, _ = _parse_range(
        f"신청기간 - {_safe_detail_text(cells[4], identity, 'application period')}",
        "신청기간",
        identity,
    )
    start_text, end_text, period, start, end = _parse_range(
        f"교육기간 - {_safe_detail_text(cells[5], identity, 'education period')}",
        "교육기간",
        identity,
    )
    venue = _safe_detail_text(cells[6], identity, "venue")
    capacity_total = _detail_count(
        _safe_detail_text(cells[8], identity, "capacity"), identity, "capacity"
    )
    capacity_wait_total = _detail_count(
        _safe_detail_text(cells[9], identity, "wait capacity"), identity, "wait capacity"
    )
    if (
        title != _clean(listed.get("title"))
        or (apply_start, apply_end, apply_period)
        != (
            _clean(listed.get("apply_start")),
            _clean(listed.get("apply_end")),
            _clean(listed.get("apply_period")),
        )
        or (start_text, end_text, period, start, end)
        != (
            _clean(listed.get("start_text")),
            _clean(listed.get("end_text")),
            _clean(listed.get("period")),
            listed.get("start"),
            listed.get("end"),
        )
        or capacity_total != int(listed.get("capacity_total") or 0)
    ):
        raise GangjinContractError(f"course {identity}: list/detail safe fields mismatch")
    areas = soup.select("#content > div.btn_center")
    controls: list[tuple[str, str]] = []
    for area in areas:
        links = area.find_all("a", recursive=False)
        if len(links) != 1:
            raise GangjinContractError(f"course {identity}: application area changed")
        images = links[0].select(":scope > img[alt]")
        if len(images) != 1 or _clean(images[0].get("alt")) != "접수하기":
            raise GangjinContractError(f"course {identity}: application label changed")
        controls.append(_owned_url(links[0].get("href"), scope, frozenset({"write"})))
    controls = sorted(set(controls))
    expected_application = gangjin_application_url(scope, identity)
    if _clean(listed.get("status")) == "OPEN":
        if controls != [(identity, expected_application)]:
            raise GangjinContractError(f"course {identity}: identity-bound application control changed")
        application_url = expected_application
    elif controls:
        raise GangjinContractError(f"course {identity}: inactive detail exposes application control")
    else:
        application_url = ""
    return_links = soup.select("#content > #board_basic_view > div.lecture_btn_box > a[href]")
    if len(return_links) != 1:
        raise GangjinContractError(f"course {identity}: list return changed")
    scope_url = f"https://{GANGJIN_HOST}{GANGJIN_SCOPE_PATHS[scope]}"
    return_parsed = urlparse(urljoin(scope_url, _clean(return_links[0].get("href"))))
    if (
        _clean(return_links[0].get_text(" ", strip=True)) != "목록"
        or return_parsed.scheme != "https"
        or (return_parsed.hostname or "").rstrip(".").lower() != GANGJIN_HOST
        or return_parsed.path != GANGJIN_SCOPE_PATHS[scope]
        or parse_qs(return_parsed.query, keep_blank_values=True) != {"page": [""]}
        or return_parsed.fragment
    ):
        raise GangjinContractError(f"course {identity}: list return ownership changed")
    visible_control = bool(application_url)
    branch = _branch_name(venue)
    return {
        "provider": GANGJIN_PROVIDER,
        "provider_course_id": f"{GANGJIN_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(venue),
        "preserve_branch": True,
        "category": "교육·강좌",
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if visible_control else "INFO_ONLY",
        "application_method": "온라인" if visible_control else "",
        "application_methods": ["온라인"] if visible_control else [],
        "reservation_available": visible_control,
        "status": _clean(listed.get("status")),
        "fee": fee_text,
        "fee_amount": fee_amount,
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "schedule_raw": period,
        "capacity": f"{capacity_total}명",
        "capacity_current": int(listed.get("capacity_current") or 0),
        "capacity_wait": int(listed.get("wait_current") or 0),
        "capacity_total": capacity_total,
        "capacity_wait_total": capacity_wait_total,
        "target": target,
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": GANGJIN_PARSER,
        "municipality_code": GANGJIN_MUNICIPALITY_CODE,
        "municipality_full_name": GANGJIN_MUNICIPALITY_NAME,
        "raw_fields": {
            "scope": scope,
            "identity": identity,
            "source_page": int(listed.get("source_page") or 0),
            "source_position": int(listed.get("source_position") or 0),
            "source_opening_state": _clean(listed.get("source_opening_state")),
            "source_status": _clean(listed.get("source_status")),
            "source_period": period,
            "source_apply_period": apply_period,
            "source_capacity_current": int(listed.get("capacity_current") or 0),
            "source_wait_current": int(listed.get("wait_current") or 0),
            "source_capacity_total": int(listed.get("capacity_total") or 0),
            "source_generic_login_indicator_present": bool(
                listed.get("generic_login_indicator")
            ),
            "detail_fee": fee_text,
            "detail_target": target,
            "detail_venue": venue,
            "detail_capacity_total": capacity_total,
            "detail_capacity_wait_total": capacity_wait_total,
            "detail_verified": True,
            "visible_application_control_present": visible_control,
            "application_control_contract": (
                "identity_bound_write_route_not_fetched"
                if visible_control
                else "verified_no_control"
            ),
            "service_family": "education",
        },
    }


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= _SAFE_RAW_FIELDS:
        errors.append("raw_fields exceeded PII-safe allowlist")
    payload = repr(
        {key: value for key, value in row.items() if key not in {"raw_url", "application_url"}}
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact value persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form description persisted")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _base_meta(error: str = "") -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "semantic_duplicate_group_count": 0,
        "historical_semantic_duplicate_group_count": 0,
        "historical_reversed_application_period_count": 0,
        "historical_reversed_education_period_count": 0,
        "current_reversed_application_period_count": 0,
        "current_reversed_education_period_count": 0,
        "source_application_control_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": GANGJIN_MUNICIPALITY_CODE,
        "municipality_name": GANGJIN_MUNICIPALITY_NAME,
        "canonical_url": GANGJIN_CANONICAL_URL,
        "ownership_scope": GANGJIN_OWNERSHIP_SCOPE,
        "candidate_audit": {
            key: dict(value) for key, value in GANGJIN_CANDIDATE_AUDIT.items()
        },
        "discovery_audit": dict(GANGJIN_DISCOVERY_AUDIT),
        "municipality_coverage": [GANGJIN_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(GANGJIN_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
    }


def collect_gangjin_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GANGJIN_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future two-scope Gangjin snapshot."""

    meta = _base_meta()
    if not is_gangjin_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Gangjin education catalogue owner"
        )
        return [], GANGJIN_PARSER, meta
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
        return [], GANGJIN_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GANGJIN_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, GANGJIN_MAX_WORKERS)
    meta["network_concurrency"] = workers

    def fetch_list(
        scope: str,
        page: int,
        *,
        total: Optional[int] = None,
        pages: Optional[int] = None,
        sentinel: bool = False,
    ) -> _ListPage:
        soup = _fetch_soup(
            gangjin_list_url(scope, page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(
            soup,
            scope,
            page,
            expected_total=total,
            expected_pages=pages,
            sentinel=sentinel,
        )

    first_pages: dict[str, _ListPage] = {}
    first_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(GANGJIN_SCOPE_PATHS), workers)) as pool:
        futures = {pool.submit(fetch_list, scope, 1): scope for scope in GANGJIN_SCOPE_PATHS}
        for future in as_completed(futures):
            scope = futures[future]
            try:
                first_pages[scope] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                first_errors.append(f"{scope} page 1: {type(exc).__name__}: {_clean(exc)}")
    if first_errors or set(first_pages) != set(GANGJIN_SCOPE_PATHS):
        meta["configured_collection_error"] = "; ".join(first_errors or ["first pages missing"])
        return [], GANGJIN_PARSER, meta

    required = sum(first.source_pages + 3 for first in first_pages.values())
    meta.update(
        {
            "required_list_requests": required,
            "declared_source_rows_by_scope": {
                scope: first_pages[scope].total for scope in GANGJIN_SCOPE_PATHS
            },
            "declared_data_pages_by_scope": {
                scope: first_pages[scope].source_pages for scope in GANGJIN_SCOPE_PATHS
            },
            "declared_source_rows": sum(first.total for first in first_pages.values()),
            "declared_data_pages": sum(first.source_pages for first in first_pages.values()),
        }
    )
    if required > max_pages:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"max_pages cap allows {max_pages} of {required} required list requests"
                ),
            }
        )
        return [], GANGJIN_PARSER, meta

    jobs: list[tuple[str, str, int, bool]] = []
    for scope, first in first_pages.items():
        jobs.extend((scope, "data", page, False) for page in range(2, first.source_pages + 1))
        jobs.extend(
            (
                (scope, "sentinel", first.source_pages + 1, True),
                (scope, "first_recheck", 1, False),
                (scope, "last_recheck", first.source_pages, False),
            )
        )
    parsed_jobs: dict[tuple[str, str, int], _ListPage] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                fetch_list,
                scope,
                page,
                total=first_pages[scope].total,
                pages=first_pages[scope].source_pages,
                sentinel=sentinel,
            ): (scope, kind, page)
            for scope, kind, page, sentinel in jobs
        }
        for future in as_completed(futures):
            scope, kind, page = futures[future]
            try:
                parsed_jobs[(scope, kind, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(f"{scope} {kind} page {page}: {type(exc).__name__}: {_clean(exc)}")

    page_rows: dict[tuple[str, int], tuple[dict[str, Any], ...]] = {}
    per_scope_rows: dict[str, int] = {}
    per_scope_pages: dict[str, int] = {}
    sentinel_count = 0
    recheck_count = 0
    for scope, first in first_pages.items():
        for page in range(1, first.source_pages + 1):
            parsed = first if page == 1 else parsed_jobs.get((scope, "data", page))
            if parsed is None:
                errors.append(f"{scope} data page {page}: response missing")
                continue
            page_rows[(scope, page)] = parsed.rows
        sentinel = parsed_jobs.get((scope, "sentinel", first.source_pages + 1))
        if sentinel is None:
            errors.append(f"{scope}: immediate post-last sentinel response missing")
        elif sentinel.rows or not sentinel.empty_marker:
            errors.append(f"{scope}: immediate post-last sentinel is not empty")
        else:
            sentinel_count += 1
        first_recheck = parsed_jobs.get((scope, "first_recheck", 1))
        last_recheck = parsed_jobs.get((scope, "last_recheck", first.source_pages))
        if first_recheck is None or last_recheck is None:
            errors.append(f"{scope}: first/last stability recheck missing")
        else:
            recheck_count += 2
            last_rows = page_rows.get((scope, first.source_pages), ())
            if (
                _page_signature(first_recheck.rows) != _page_signature(first.rows)
                or _page_signature(last_recheck.rows) != _page_signature(last_rows)
            ):
                errors.append(f"{scope}: first/last boundary changed")
        rows = sum(len(page_rows.get((scope, page), ())) for page in range(1, first.source_pages + 1))
        per_scope_rows[scope] = rows
        per_scope_pages[scope] = sum((scope, page) in page_rows for page in range(1, first.source_pages + 1))
        if rows != first.total:
            errors.append(f"{scope}: complete source row count {rows} != {first.total}")

    listed = [
        row
        for scope in GANGJIN_SCOPE_PATHS
        for page in range(1, first_pages[scope].source_pages + 1)
        for row in page_rows.get((scope, page), ())
    ]
    identities = [_clean(row.get("identity")) for row in listed]
    raw_urls = [_clean(row.get("raw_url")) for row in listed]
    identity_duplicates = len(identities) - len(set(identities))
    raw_url_duplicates = len(raw_urls) - len(set(raw_urls))
    if identity_duplicates:
        errors.append(f"{identity_duplicates} duplicate official identities")
    if raw_url_duplicates:
        errors.append(f"{raw_url_duplicates} duplicate canonical detail URLs")
    for scope in GANGJIN_SCOPE_PATHS:
        scope_ids = [int(row["identity"]) for row in listed if row["scope"] == scope]
        if any(left <= right for left, right in zip(scope_ids, scope_ids[1:])):
            errors.append(f"{scope}: official identities are not strictly descending")
    current_listed = [row for row in listed if row["end"] >= cutoff]
    historical_listed = [row for row in listed if row["end"] < cutoff]
    current_reversed_apply = sum(row["apply_start_date"] > row["apply_end_date"] for row in current_listed)
    current_reversed_education = sum(row["start"] > row["end"] for row in current_listed)
    historical_reversed_apply = sum(row["apply_start_date"] > row["apply_end_date"] for row in historical_listed)
    historical_reversed_education = sum(row["start"] > row["end"] for row in historical_listed)
    if current_reversed_apply:
        errors.append(f"{current_reversed_apply} current reversed application periods")
    if current_reversed_education:
        errors.append(f"{current_reversed_education} current reversed education periods")
    historical_semantic = Counter(
        (_clean(row.get("scope")), _normalized(row.get("title")), _clean(row.get("period")))
        for row in historical_listed
    )
    historical_semantic_groups = sum(value > 1 for value in historical_semantic.values())
    list_complete = bool(
        not errors
        and meta["list_requests"] == required
        and sentinel_count == len(GANGJIN_SCOPE_PATHS)
        and recheck_count == 2 * len(GANGJIN_SCOPE_PATHS)
        and len(listed) == sum(first.total for first in first_pages.values())
    )
    meta.update(
        {
            "source_total": len(listed),
            "source_rows": len(listed),
            "source_rows_by_scope": per_scope_rows,
            "data_pages": sum(per_scope_pages.values()),
            "data_pages_by_scope": per_scope_pages,
            "sentinel_requests": sentinel_count,
            "stability_rechecks": recheck_count,
            "current_source_count": len(current_listed),
            "current_scope_counts": dict(Counter(row["scope"] for row in current_listed)),
            "expired_count": len(historical_listed),
            "identity_duplicate_count": identity_duplicates,
            "raw_url_duplicate_count": raw_url_duplicates,
            "historical_semantic_duplicate_group_count": historical_semantic_groups,
            "historical_reversed_application_period_count": historical_reversed_apply,
            "historical_reversed_education_period_count": historical_reversed_education,
            "current_reversed_application_period_count": current_reversed_apply,
            "current_reversed_education_period_count": current_reversed_education,
            "source_application_control_count": sum(row["generic_login_indicator"] for row in listed),
            "source_status_counts": dict(Counter(row["source_status"] for row in listed)),
            "current_normalized_status_counts": dict(Counter(row["status"] for row in current_listed)),
            "pagination_detected": any(first.source_pages > 1 for first in first_pages.values()),
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GANGJIN_PARSER, meta
    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of {len(current_listed)} required current details"
                ),
            }
        )
        return [], GANGJIN_PARSER, meta

    meta["detail_attempts"] = len(current_listed)
    detailed: dict[str, dict[str, Any]] = {}
    detail_errors: list[str] = []

    def fetch_detail(listed_row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        identity = _clean(listed_row.get("identity"))
        soup = _fetch_soup(
            _clean(listed_row.get("raw_url")),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return identity, _parse_detail(soup, listed_row)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_detail, row): row for row in current_listed}
        for future in as_completed(futures):
            listed_row = futures[future]
            identity = _clean(listed_row.get("identity"))
            try:
                parsed_identity, parsed = future.result()
                if parsed_identity in detailed:
                    raise GangjinContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(f"detail {identity}: {type(exc).__name__}: {_clean(exc)}")
    meta["detail_errors"] = len(detail_errors)
    errors.extend(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_pages"] == len(current_listed)
        and len(detailed) == len(current_listed)
    )
    ordered = [detailed[row["identity"]] for row in current_listed if row["identity"] in detailed]
    semantic = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("raw_fields", {}).get("detail_venue")),
        )
        for row in ordered
    )
    semantic_groups = sum(value > 1 for value in semantic.values())
    if semantic_groups:
        errors.append(f"{semantic_groups} current semantic duplicate groups")
    controls_complete = bool(
        details_complete
        and all(
            (row.get("status") == "OPEN")
            == bool(row.get("raw_fields", {}).get("visible_application_control_present"))
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and controls_complete and not semantic_groups and not errors:
        for row in ordered:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            try:
                result = list(deduper(ordered))
            except Exception as exc:
                errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
                result = []
            if len(result) != len(ordered):
                errors.append(
                    f"dedupe changed official identity cardinality {len(ordered)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and controls_complete
        and not semantic_groups
        and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "returned_count": len(result),
            "semantic_duplicate_group_count": semantic_groups,
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "current_branch_names": sorted({_clean(row.get("branch")) for row in result}),
            "venue_counts": dict(
                Counter(_clean(row.get("raw_fields", {}).get("detail_venue")) for row in result)
            ),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "visible_public_application_control_count": sum(
                bool(row.get("raw_fields", {}).get("visible_application_control_present"))
                for row in ordered
            ),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete official Gangjin education catalogues have no current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GANGJIN_PARSER, meta


collect = collect_gangjin_education


__all__ = [
    "GANGJIN_CANDIDATE_AUDIT",
    "GANGJIN_CANDIDATE_ID",
    "GANGJIN_CANONICAL_URL",
    "GANGJIN_DIGITAL_URL",
    "GANGJIN_DISCOVERY_AUDIT",
    "GANGJIN_MUNICIPALITY_CODE",
    "GANGJIN_MUNICIPALITY_NAME",
    "GANGJIN_PARSER",
    "GANGJIN_PROVIDER",
    "collect",
    "collect_gangjin_education",
    "gangjin_application_url",
    "gangjin_detail_url",
    "gangjin_list_url",
    "is_gangjin_candidate_alias",
    "is_gangjin_education_target",
    "is_target",
]
