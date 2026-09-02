"""Fail-closed collector for Jangheung-gun's official lifelong courses.

The municipal candidate points at the lifelong-learning home page.  That page
only republishes cards owned by the existing ``course/apply`` catalogue and
is therefore a discovery/duplicate landing rather than a second owner.  The
catalogue is traversed through its explicit ``all`` status, reconciled against
the category total and official descending row numbers, followed by the
immediate empty page and stable first/last rechecks.

Only current/future details are opened.  Detail access is deliberately
allowlisted: contact, lecturer and free-form introduction cells are never
read or persisted.  A current institution name must be present in the
official ``교육정보`` field before it can become an exact branch.  Online
application controls are accepted only when they are visibly bound to the
same course identity; the application form itself is never fetched because
it is a login-gated PII boundary.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


JANGHEUNG_PROVIDER = "MUNI_WWW_JANGHEUNG_GO_KR_0392DE78"
JANGHEUNG_DUPLICATE_LANDING_PROVIDER = "MUNI_WWW_JANGHEUNG_GO_KR_5046AC44"
JANGHEUNG_CANDIDATE_ID = "MUNI_IR_70E0A269C0DD"
JANGHEUNG_EXISTING_OWNER_AUDIT_ID = "EXISTING_MUNI_WWW_JANGHEUNG_GO_KR_0392DE78"
JANGHEUNG_DUPLICATE_OWNER_AUDIT_ID = "EXISTING_MUNI_WWW_JANGHEUNG_GO_KR_5046AC44"
JANGHEUNG_GUIDE_AUDIT_ID = "OFFICIAL_JANGHEUNG_APPLICATION_GUIDE"
JANGHEUNG_SCHEDULE_AUDIT_ID = "OFFICIAL_JANGHEUNG_INFORMATION_CALENDAR"
JANGHEUNG_PLACES_AUDIT_ID = "OFFICIAL_JANGHEUNG_STALE_INSTITUTION_DIRECTORY"
JANGHEUNG_EDUCATION_SUPPORT_AUDIT_ID = "SEPARATE_JANGHEUNG_EDUCATION_SUPPORT_NEWS"

JANGHEUNG_HOST = "www.jangheung.go.kr"
JANGHEUNG_PATH = "/lifelong/course/apply"
JANGHEUNG_URL = f"https://{JANGHEUNG_HOST}{JANGHEUNG_PATH}"
JANGHEUNG_ROOT_URL = f"https://{JANGHEUNG_HOST}/lifelong"
JANGHEUNG_GUIDE_URL = f"https://{JANGHEUNG_HOST}/lifelong/course/guide"
JANGHEUNG_SCHEDULE_URL = f"https://{JANGHEUNG_HOST}/lifelong/course/schedule"
JANGHEUNG_PLACES_URL = f"https://{JANGHEUNG_HOST}/lifelong/course/edu_place"
JANGHEUNG_EDUCATION_SUPPORT_URL = (
    "https://jhed.jne.go.kr/jhed/na/ntt/selectNttList.do?mi=14913&bbsId=14913"
)
JANGHEUNG_MUNICIPALITY_CODE = "1277000000"
JANGHEUNG_MUNICIPALITY_NAME = "전남광주통합특별시 장흥군"
JANGHEUNG_MAX_WORKERS = 6
JANGHEUNG_FETCH_ATTEMPTS = 3
JANGHEUNG_RETRY_BACKOFF_SECONDS = 0.2
JANGHEUNG_MAX_HTML_BYTES = 4_000_000
JANGHEUNG_PARSER = (
    "jangheung_official_lifelong_all_pages+empty_sentinel+stable_boundaries+"
    "current_detail_branches+identity_bound_login_controls+pii_allowlist"
)
JANGHEUNG_OWNERSHIP_SCOPE = "jangheung_official_lifelong_course_apply_catalogue"

JANGHEUNG_CATEGORIES: tuple[str, ...] = (
    "문화예술",
    "기초문해",
    "인문교양",
    "직업능력",
    "학력보완",
    "시민참여",
)
JANGHEUNG_SEARCH_STATUSES: tuple[tuple[str, str], ...] = (
    ("all", "전체"),
    ("1", "접수대기"),
    ("2", "접수중"),
    ("4", "수강대기"),
    ("8", "수강중"),
    ("16", "수강종료"),
    ("32", "강의종료"),
    ("64", "폐강"),
    ("128", "수강확정"),
)
JANGHEUNG_SEARCH_TYPES: tuple[tuple[str, str], ...] = (
    ("title", "강좌명"),
    ("lecturer", "강사명"),
    ("institute", "교육기관"),
)
JANGHEUNG_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "수강대기": "CLOSED",
    "수강중": "CLOSED",
    "수강종료": "CLOSED",
    "강의종료": "CLOSED",
    "수강확정": "CLOSED",
    "폐강": "CANCELLED",
}

JANGHEUNG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    JANGHEUNG_EXISTING_OWNER_AUDIT_ID: {
        "decision": "include_existing_provider_as_canonical_course_catalogue_owner",
        "provider": JANGHEUNG_PROVIDER,
        "url": JANGHEUNG_URL,
        "owner": JANGHEUNG_PROVIDER,
    },
    JANGHEUNG_DUPLICATE_OWNER_AUDIT_ID: {
        "decision": "exclude_duplicate_landing_republishing_canonical_course_identities",
        "provider": JANGHEUNG_DUPLICATE_LANDING_PROVIDER,
        "url": JANGHEUNG_ROOT_URL,
        "owner": JANGHEUNG_PROVIDER,
    },
    JANGHEUNG_CANDIDATE_ID: {
        "decision": "roll_candidate_landing_into_existing_catalogue_owner",
        "provider": JANGHEUNG_DUPLICATE_LANDING_PROVIDER,
        "url": "http:" "//www.jangheung.go.kr/lifelong",
        "normalized_url": JANGHEUNG_ROOT_URL,
        "owner": JANGHEUNG_PROVIDER,
    },
    JANGHEUNG_GUIDE_AUDIT_ID: {
        "decision": "exclude_application_instructions_without_course_rows",
        "provider": JANGHEUNG_PROVIDER,
        "url": JANGHEUNG_GUIDE_URL,
        "owner": JANGHEUNG_PROVIDER,
    },
    JANGHEUNG_SCHEDULE_AUDIT_ID: {
        "decision": "exclude_independent_information_calendar_without_booking_identity",
        "provider": JANGHEUNG_PROVIDER,
        "url": JANGHEUNG_SCHEDULE_URL,
        "owner": "jangheung_lifelong_information_calendar",
    },
    JANGHEUNG_PLACES_AUDIT_ID: {
        "decision": "exclude_stale_institution_directory_not_course_branch_authority",
        "provider": JANGHEUNG_PROVIDER,
        "url": JANGHEUNG_PLACES_URL,
        "owner": "seventeen_separate_education_institutions",
        "last_updated": "2020-12-03",
    },
    JANGHEUNG_EDUCATION_SUPPORT_AUDIT_ID: {
        "decision": "exclude_separate_education_support_news_board_not_course_catalogue",
        "provider": "SEPARATE_JHED_JNE_GO_KR",
        "url": JANGHEUNG_EDUCATION_SUPPORT_URL,
        "owner": "jangheung_education_support_office",
    },
}

JANGHEUNG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": JANGHEUNG_URL,
    "source_rows": 2,
    "data_pages": 1,
    "required_list_requests": 4,
    "current_or_future_rows": 0,
    "expired_rows": 2,
    "source_status_counts": {"수강종료": 2},
    "source_category_counts": {"문화예술": 2},
    "source_identities": ["12", "8"],
    "duplicate_landing_identity_overlap": 2,
    "duplicate_landing_status_note": (
        "landing labels both rows 접수마감 while canonical catalogue labels 수강종료"
    ),
    "historical_details_manually_verified": 2,
    "historical_visible_application_controls": 0,
    "current_branch_names": [],
    "historical_detail_education_info": ["장흥군", ""],
    "stale_institution_directory_count": 17,
    "stale_institution_directory_last_updated": "2020-12-03",
    "education_calendar_current_events": 0,
    "list_title_variants": ["with_all_filter_label", "without_all_filter_label"],
    "identity_duplicate_count": 0,
    "conclusion": "candidate_rolls_up_to_existing_apply_provider",
}

JANGHEUNG_PII_FIELDS_DISCARDED = (
    "강 사 명",
    "문의전화",
    "강사명",
    "강좌소개",
    "application_form_values",
    "member_profile",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class JangheungContractError(ValueError):
    """Raised when the audited official catalogue contract changes."""


@dataclass(frozen=True)
class _ListPage:
    requested_page: int
    total: int
    source_pages: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_CSRF_RE = re.compile(r"^[0-9a-f]{64}$")
_TOTAL_RE = re.compile(r"^전체\s*\(\s*(?P<total>[\d,]+)\s*\)$")
_CATEGORY_RE = re.compile(r"^(?P<name>.+?)\s*\(\s*(?P<count>[\d,]+)\s*\)$")
_CAPTION_RE = re.compile(
    r'^이표는\s+강좌관리목록로\s+6컬럼,\s*(?P<rows>[\d,]+)로우로\s+구성되어\s+있습니다\..*"$'
)
_DATE_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_DATETIME_RANGE_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})$"
)
_FEE_RE = re.compile(r"^(?P<amount>[\d,]+)\s*원$")
_COUNT_RE = re.compile(r"^(?P<count>[\d,]+)$")
_WAIT_RE = re.compile(r"^\(\s*(?P<count>[\d,]+)\s*\)$")
_CAPACITY_RE = re.compile(r"^(?P<count>[\d,]+)\s*명$")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIST_HEADERS = (
    "번호",
    "분류",
    "강좌정보",
    "신청(대기)/정원",
    "수강료",
    "접수현황",
)
_DETAIL_ROW_LABELS = (
    ("분류",),
    ("강좌명(기수)",),
    ("교육정보", "교육대상"),
    ("수강료", "문의전화"),
    ("신청기간", "교육기간"),
    ("강사명", "교육장소"),
    ("모집정원", "모집대기인원"),
    ("강좌소개",),
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_page",
        "source_row_number",
        "source_status",
        "source_category",
        "source_period",
        "source_apply_period",
        "source_fee",
        "source_capacity_current",
        "source_wait_current",
        "source_capacity_total",
        "source_institution",
        "detail_target",
        "detail_venue",
        "detail_capacity_wait_total",
        "detail_verified",
        "visible_application_control_present",
        "application_control_contract",
        "service_family",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "contact",
        "phone",
        "email",
        "description_html",
        "detail_description",
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


def is_jangheung_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != JANGHEUNG_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == JANGHEUNG_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == JANGHEUNG_PATH
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_jangheung_education_target


def is_jangheung_candidate_alias(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    return bool(
        candidate == JANGHEUNG_CANDIDATE_ID
        or provider == JANGHEUNG_DUPLICATE_LANDING_PROVIDER
        or url
        in {
            "http:" "//www.jangheung.go.kr/lifelong",
            JANGHEUNG_ROOT_URL,
            JANGHEUNG_GUIDE_URL,
            JANGHEUNG_SCHEDULE_URL,
            JANGHEUNG_PLACES_URL,
            JANGHEUNG_EDUCATION_SUPPORT_URL,
        }
    )


def jangheung_list_url(page: int) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    return f"{JANGHEUNG_URL}?" + urlencode(
        (("page", str(page)), ("search_status", "all"))
    )


def jangheung_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return f"{JANGHEUNG_URL}?" + urlencode((("idx", value), ("mode", "view")))


def jangheung_application_url(identity: Any) -> str:
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    return f"{JANGHEUNG_URL}?" + urlencode(
        (("lecture_idx", value), ("mode", "reserve_form2"))
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
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": JANGHEUNG_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    status = int(getattr(response, "status_code", 0))
    if status != 200:
        raise JangheungContractError(f"unexpected HTTP status {status}")
    if getattr(response, "headers", {}).get("Location"):
        raise JangheungContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise JangheungContractError("empty HTTP response")
    if len(content) > JANGHEUNG_MAX_HTML_BYTES:
        raise JangheungContractError("HTTP response exceeded HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if not value or len(value) > JANGHEUNG_MAX_HTML_BYTES:
            raise JangheungContractError("invalid HTML fixture size")
        return BeautifulSoup(value, "lxml", from_encoding="utf-8")
    if isinstance(value, str):
        if not value or len(value.encode("utf-8")) > JANGHEUNG_MAX_HTML_BYTES:
            raise JangheungContractError("invalid HTML fixture size")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor response")
    if not content or len(content) > JANGHEUNG_MAX_HTML_BYTES:
        raise JangheungContractError("invalid HTTP response size")
    return BeautifulSoup(content, "lxml", from_encoding="utf-8")


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
    for attempt in range(JANGHEUNG_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < JANGHEUNG_FETCH_ATTEMPTS:
                time.sleep(JANGHEUNG_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _parse_owned_url(value: Any) -> Any:
    parsed = urlparse(urljoin(JANGHEUNG_URL, _clean(value)))
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").rstrip(".").lower() != JANGHEUNG_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise JangheungContractError("course link ownership changed")
    return parsed


def _detail_link(value: Any) -> tuple[str, str]:
    parsed = _parse_owned_url(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "idx")
    if (
        parsed.path != JANGHEUNG_PATH
        or set(query) != {"idx", "mode"}
        or _single_query(query, "mode") != "view"
        or _POSITIVE_ID_RE.fullmatch(identity) is None
    ):
        raise JangheungContractError("course detail identity link changed")
    return identity, jangheung_detail_url(identity)


def _application_link(value: Any) -> tuple[str, str]:
    parsed = _parse_owned_url(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _single_query(query, "lecture_idx")
    if (
        parsed.path != JANGHEUNG_PATH
        or set(query) != {"lecture_idx", "mode"}
        or _single_query(query, "mode") != "reserve_form2"
        or _POSITIVE_ID_RE.fullmatch(identity) is None
    ):
        raise JangheungContractError("course application identity link changed")
    return identity, jangheung_application_url(identity)


def _options(node: Any) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in node.find_all("option", recursive=False)
    )


def _validate_list_document(soup: BeautifulSoup, page: int, *, sentinel: bool) -> None:
    titles = soup.select("head > title")
    filtered = (
        f"{page} 페이지 목록보기 < (전체) < 강좌신청 < 강좌정보 - 장흥군 평생교육"
    )
    expected = {
        filtered,
        f"{page} 페이지 목록보기 < 강좌신청 < 강좌정보 - 장흥군 평생교육",
    }
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) not in expected:
        raise JangheungContractError(f"page {page}: official list title changed")
    forms = soup.select("form#list_search")
    if len(forms) != 1:
        raise JangheungContractError(f"page {page}: list search form changed")
    form = forms[0]
    action = _parse_owned_url(form.get("action"))
    if (
        tuple(form.get("class") or ()) != ("list_sch2",)
        or form.has_attr("method")
        or action.path != JANGHEUNG_PATH
        or action.query
    ):
        raise JangheungContractError(f"page {page}: list search ownership changed")
    csrf = form.select('input[type="hidden"][name="csrf_token"]')
    if len(csrf) != 1 or _CSRF_RE.fullmatch(_clean(csrf[0].get("value"))) is None:
        raise JangheungContractError(f"page {page}: CSRF field changed")
    statuses = form.select('select#search_status[name="search_status"]')
    search_types = form.select('select#search_type[name="search_type"]')
    if len(statuses) != 1 or _options(statuses[0]) != JANGHEUNG_SEARCH_STATUSES:
        raise JangheungContractError(f"page {page}: status taxonomy changed")
    if len(search_types) != 1 or _options(search_types[0]) != JANGHEUNG_SEARCH_TYPES:
        raise JangheungContractError(f"page {page}: search taxonomy changed")
    text = form.select('input[type="text"]#search_word[name="search_word"]')
    starts = form.select('input[type="text"]#search_startdate[name="search_startdate"]')
    ends = form.select('input[type="text"]#search_enddate[name="search_enddate"]')
    submit = form.select('input[type="submit"][value="검색"]')
    if (
        len(text) != 1
        or _clean(text[0].get("value"))
        or len(starts) != 1
        or _clean(starts[0].get("value"))
        or tuple(starts[0].get("class") or ()) != ("onlydate",)
        or len(ends) != 1
        or _clean(ends[0].get("value"))
        or tuple(ends[0].get("class") or ()) != ("onlydate",)
        or len(submit) != 1
    ):
        raise JangheungContractError(f"page {page}: empty all-history search changed")


def _category_contract(soup: BeautifulSoup, page: int) -> tuple[int, dict[str, int]]:
    roots = soup.select("ul.cate_list")
    if len(roots) != 1:
        raise JangheungContractError(f"page {page}: category summary changed")
    items = roots[0].find_all("li", recursive=False)
    if len(items) != len(JANGHEUNG_CATEGORIES) + 1:
        raise JangheungContractError(f"page {page}: category count changed")
    if tuple(items[0].get("class") or ()) != ("first", "on"):
        raise JangheungContractError(f"page {page}: all-category marker changed")
    total_match = _TOTAL_RE.fullmatch(_clean(items[0].get_text(" ", strip=True)))
    if total_match is None or len(items[0].select(":scope > span")) != 1:
        raise JangheungContractError(f"page {page}: declared total changed")
    total = int(total_match.group("total").replace(",", ""))
    counts: dict[str, int] = {}
    for expected, item in zip(JANGHEUNG_CATEGORIES, items[1:]):
        links = item.select(":scope > a[href]")
        if len(links) != 1 or len(links[0].select(":scope > span")) != 1:
            raise JangheungContractError(f"page {page}: category link changed")
        match = _CATEGORY_RE.fullmatch(_clean(links[0].get_text(" ", strip=True)))
        parsed = _parse_owned_url(links[0].get("href"))
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            match is None
            or match.group("name") != expected
            or parsed.path != JANGHEUNG_PATH
            or set(query) != {"category_1", "search_status"}
            or _single_query(query, "category_1") != expected
            or _single_query(query, "search_status") != "all"
        ):
            raise JangheungContractError(f"page {page}: category taxonomy changed")
        counts[expected] = int(match.group("count").replace(",", ""))
    if sum(counts.values()) != total:
        raise JangheungContractError(f"page {page}: category totals do not reconcile")
    return total, counts


def _pagination_link_page(value: Any, *, require_title: bool = False, node: Any = None) -> int:
    parsed = _parse_owned_url(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    raw = _single_query(query, "page")
    if (
        parsed.path != JANGHEUNG_PATH
        or set(query) != {"page", "search_status"}
        or _single_query(query, "search_status") != "all"
        or _POSITIVE_ID_RE.fullmatch(raw) is None
    ):
        raise JangheungContractError("pagination link changed")
    page = int(raw)
    if require_title and _clean(node.get("title") if node is not None else "") != f"{page} 페이지":
        raise JangheungContractError("pagination title changed")
    return page


def _first_source_pages(soup: BeautifulSoup) -> int:
    active = soup.select("div.list_paging > div.num > a.on")
    if (
        len(active) != 1
        or _clean(active[0].get_text(" ", strip=True)) != "1"
        or active[0].has_attr("href")
    ):
        raise JangheungContractError("first-page active marker changed")
    last = soup.select("div.list_paging > div.num > a.last[href]")
    if not last:
        return 1
    if len(last) != 1:
        raise JangheungContractError("last-page marker changed")
    page = _pagination_link_page(last[0].get("href"))
    if page <= 1:
        raise JangheungContractError("last-page boundary changed")
    return page


def _validate_pagination(
    soup: BeautifulSoup,
    *,
    requested_page: int,
    source_pages: int,
    sentinel: bool,
) -> None:
    roots = soup.select("div.list_paging > div.num")
    if len(roots) != 1:
        raise JangheungContractError(f"page {requested_page}: pagination changed")
    active = roots[0].select(":scope > a.on")
    last = roots[0].select(":scope > a.last[href]")
    if sentinel:
        if active or last:
            raise JangheungContractError("post-last sentinel has an active/last marker")
        boundaries = [
            node
            for node in roots[0].select(":scope > a[title][href]")
            if _clean(node.get("title")) == f"{source_pages} 페이지"
        ]
        if (
            len(boundaries) != 1
            or _pagination_link_page(
                boundaries[0].get("href"), require_title=True, node=boundaries[0]
            )
            != source_pages
        ):
            raise JangheungContractError("post-last sentinel boundary link changed")
        return
    if (
        len(active) != 1
        or _clean(active[0].get_text(" ", strip=True)) != str(requested_page)
        or active[0].has_attr("href")
    ):
        raise JangheungContractError(f"page {requested_page}: active marker mismatch")
    if requested_page < source_pages:
        if len(last) != 1 or _pagination_link_page(last[0].get("href")) != source_pages:
            raise JangheungContractError(f"page {requested_page}: last boundary mismatch")
    elif last:
        raise JangheungContractError(f"page {requested_page}: terminal page exposes last link")


def _date_range(value: Any, identity: str, label: str) -> tuple[date, date, str]:
    text = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(text)
    if match is None:
        raise JangheungContractError(f"course {identity}: {label} changed")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    if start > end:
        raise JangheungContractError(f"course {identity}: reversed {label}")
    return start, end, text


def _datetime_range(value: Any, identity: str) -> tuple[str, str, str]:
    text = _clean(value)
    match = _DATETIME_RANGE_RE.fullmatch(text)
    if match is None:
        raise JangheungContractError(f"course {identity}: application period changed")
    start = datetime.fromisoformat(match.group("start"))
    end = datetime.fromisoformat(match.group("end"))
    if start > end:
        raise JangheungContractError(f"course {identity}: reversed application period")
    return match.group("start"), match.group("end"), text


def _fee(value: Any, identity: str) -> tuple[str, int]:
    text = _clean(value)
    if text == "무료":
        return text, 0
    match = _FEE_RE.fullmatch(text)
    if match is None:
        raise JangheungContractError(f"course {identity}: fee changed")
    return text, int(match.group("amount").replace(",", ""))


def _count(value: Any, regex: re.Pattern[str], identity: str, label: str) -> int:
    match = regex.fullmatch(_clean(value))
    if match is None:
        raise JangheungContractError(f"course {identity}: {label} changed")
    return int(match.group("count").replace(",", ""))


def _status_and_control(cell: Any, identity: str) -> tuple[str, str, str]:
    nodes = cell.select(":scope > span.s_bt, :scope > a.s_bt")
    if len(nodes) != 1:
        raise JangheungContractError(f"course {identity}: status marker changed")
    classes = tuple(nodes[0].get("class") or ())
    if (
        "s_bt" not in classes
        or len(classes) != 2
        or sum(re.fullmatch(r"bt\d+", value) is not None for value in classes) != 1
    ):
        raise JangheungContractError(f"course {identity}: status class changed")
    label = _clean(nodes[0].get_text(" ", strip=True)).replace("접수하기", "").strip()
    status = JANGHEUNG_STATUS_MAP.get(label, "")
    if not status:
        raise JangheungContractError(f"course {identity}: source status changed")
    controls: list[tuple[str, str]] = []
    for link in cell.select("a[href]"):
        query = parse_qs(urlparse(urljoin(JANGHEUNG_URL, _clean(link.get("href")))).query)
        if "lecture_idx" in query or str(_single_query(query, "mode")).startswith("reserve_form"):
            controls.append(_application_link(link.get("href")))
        else:
            raise JangheungContractError(f"course {identity}: unknown status link appeared")
    if status == "OPEN":
        if len(controls) != 1 or controls[0][0] != identity:
            raise JangheungContractError(
                f"course {identity}: open row lacks one identity-bound application control"
            )
        return label, status, controls[0][1]
    if controls:
        raise JangheungContractError(
            f"course {identity}: inactive row exposes an application control"
        )
    return label, status, ""


def _parse_list_row(row: Any, page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 6:
        raise JangheungContractError(f"page {page}: course row field count changed")
    row_number = _count(cells[0].get_text(" ", strip=True), _COUNT_RE, "unknown", "row number")
    category = _clean(cells[1].get_text(" ", strip=True))
    links = cells[2].select(":scope > a[href]")
    if len(links) != 1:
        raise JangheungContractError(f"page {page}: course detail link changed")
    identity, raw_url = _detail_link(links[0].get("href"))
    spans = links[0].find_all("span", recursive=False)
    if (
        len(spans) != 4
        or tuple(spans[0].get("class") or ()) != ("fc_blue3",)
        or any(span.get("class") for span in spans[1:])
    ):
        raise JangheungContractError(f"course {identity}: course information schema changed")
    title = _clean(spans[0].get_text(" ", strip=True))
    apply_text = _clean(spans[2].get_text(" ", strip=True))
    period_text = _clean(spans[3].get_text(" ", strip=True))
    if not title or not apply_text.startswith("신청기간 :") or not period_text.startswith("교육기간 :"):
        raise JangheungContractError(f"course {identity}: title/period labels changed")
    apply_start, apply_end, apply_period = _datetime_range(
        apply_text[len("신청기간 :") :], identity
    )
    start, end, period = _date_range(
        period_text[len("교육기간 :") :], identity, "education period"
    )
    if category not in JANGHEUNG_CATEGORIES:
        raise JangheungContractError(f"course {identity}: category changed")
    divs = cells[3].find_all("div", recursive=False)
    direct_spans = cells[3].find_all("span", recursive=False)
    applications = cells[3].select(":scope > span.apply")
    waits = cells[3].select(":scope > span.wait")
    capacities = cells[3].select(":scope > span.fix_poeple")
    if (
        len(divs) != 1
        or len(direct_spans) != 3
        or len(applications) != 1
        or len(waits) != 1
        or len(capacities) != 1
    ):
        raise JangheungContractError(f"course {identity}: capacity schema changed")
    capacity_current = _count(
        applications[0].get_text(" ", strip=True), _COUNT_RE, identity, "application count"
    )
    wait_current = _count(
        waits[0].get_text(" ", strip=True), _WAIT_RE, identity, "waiting count"
    )
    capacity_total = _count(
        capacities[0].get_text(" ", strip=True), _CAPACITY_RE, identity, "capacity"
    )
    fee, fee_amount = _fee(cells[4].get_text(" ", strip=True), identity)
    source_status, status, application_url = _status_and_control(cells[5], identity)
    return {
        "identity": identity,
        "source_page": page,
        "row_number": row_number,
        "title": title,
        "category": category,
        "source_status": source_status,
        "status": status,
        "start": start,
        "end": end,
        "period": period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "apply_period": apply_period,
        "fee": fee,
        "fee_amount": fee_amount,
        "capacity_current": capacity_current,
        "wait_current": wait_current,
        "capacity_total": capacity_total,
        "raw_url": raw_url,
        "application_url": application_url,
    }


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
    *,
    source_pages: Optional[int] = None,
    sentinel: bool = False,
) -> _ListPage:
    _validate_list_document(soup, page, sentinel=sentinel)
    total, _category_counts = _category_contract(soup, page)
    tables = soup.select("table.list_table")
    if len(tables) != 1:
        raise JangheungContractError(f"page {page}: official course table changed")
    table = tables[0]
    headers = tuple(
        _clean(node.get_text(" ", strip=True)) for node in table.select("thead > tr > th")
    )
    if headers != _LIST_HEADERS:
        raise JangheungContractError(f"page {page}: official course columns changed")
    captions = table.select(":scope > caption")
    if len(captions) != 1:
        raise JangheungContractError(f"page {page}: course table caption changed")
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise JangheungContractError(f"page {page}: course table body changed")
    source_rows = bodies[0].find_all("tr", recursive=False)
    data_rows = [row for row in source_rows if row.select_one("td.lecture_title > a[href]")]
    if data_rows:
        if len(data_rows) != len(source_rows):
            raise JangheungContractError(f"page {page}: mixed course/empty rows")
        rows = tuple(_parse_list_row(row, page) for row in data_rows)
        empty_marker = False
    else:
        if (
            len(source_rows) != 1
            or len(source_rows[0].find_all("td", recursive=False)) != 1
            or _clean(source_rows[0].find("td", recursive=False).get("colspan")) != "6"
            or _clean(source_rows[0].get_text(" ", strip=True)) != "개설된 강좌가 없습니다."
        ):
            raise JangheungContractError(f"page {page}: explicit empty marker changed")
        rows = ()
        empty_marker = True
    caption_match = _CAPTION_RE.fullmatch(_clean(captions[0].get_text(" ", strip=True)))
    if caption_match is None or int(caption_match.group("rows").replace(",", "")) != len(rows):
        raise JangheungContractError(f"page {page}: declared page row count changed")
    pages = _first_source_pages(soup) if source_pages is None else source_pages
    _validate_pagination(
        soup,
        requested_page=page,
        source_pages=pages,
        sentinel=sentinel,
    )
    return _ListPage(page, total, pages, rows, empty_marker)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("row_number")),
            _clean(row.get("title")),
            _clean(row.get("category")),
            _clean(row.get("source_status")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("fee")),
            _clean(row.get("capacity_current")),
            _clean(row.get("wait_current")),
            _clean(row.get("capacity_total")),
            _clean(row.get("application_url")),
        )
        for row in rows
    )


def _detail_values(row: Any) -> list[Any]:
    return row.find_all("td", recursive=False)


def _detail_schema(table: Any, identity: str) -> list[Any]:
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise JangheungContractError(f"course {identity}: detail body changed")
    rows = bodies[0].find_all("tr", recursive=False)
    if len(rows) != len(_DETAIL_ROW_LABELS):
        raise JangheungContractError(f"course {identity}: detail row count changed")
    for row, expected in zip(rows, _DETAIL_ROW_LABELS):
        children = row.find_all(["th", "td"], recursive=False)
        names = tuple(child.name for child in children)
        expected_names = tuple(name for _label in expected for name in ("th", "td"))
        labels = tuple(
            _clean(child.get_text(" ", strip=True)) for child in children if child.name == "th"
        )
        if names != expected_names or labels != expected:
            raise JangheungContractError(f"course {identity}: detail schema changed")
    return rows


def _safe_text(node: Any, identity: str, label: str, *, allow_empty: bool = False) -> str:
    value = _clean(node.get_text(" ", strip=True))
    if (
        (not value and not allow_empty)
        or len(value) > 300
        or _PHONE_RE.search(value)
        or _EMAIL_RE.search(value)
    ):
        raise JangheungContractError(f"course {identity}: safe {label} changed")
    return value


def _branch_name(institution: str) -> str:
    return f"{JANGHEUNG_MUNICIPALITY_NAME} / {institution}"


def _branch_code(institution: str) -> str:
    digest = hashlib.sha1(_normalized(institution).encode("utf-8")).hexdigest()[:12]
    return f"jangheung:{digest}"


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    titles = soup.select("head > title")
    expected_title = (
        f"{_clean(listed.get('title'))} < 강좌신청 < 강좌정보 - 장흥군 평생교육"
    )
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != expected_title:
        raise JangheungContractError(f"course {identity}: official detail title changed")
    tables = soup.select("#content > table.view_table")
    if len(tables) != 1:
        raise JangheungContractError(f"course {identity}: primary detail table changed")
    table = tables[0]
    captions = table.select(":scope > caption")
    if (
        len(captions) != 1
        or not _clean(captions[0].get_text(" ", strip=True)).startswith(
            "강좌접수 상세내용으로 강좌명"
        )
    ):
        raise JangheungContractError(f"course {identity}: detail caption changed")
    rows = _detail_schema(table, identity)
    category = _safe_text(_detail_values(rows[0])[0], identity, "category")
    title = _safe_text(_detail_values(rows[1])[0], identity, "title")
    identity_values = _detail_values(rows[2])
    institution = _safe_text(identity_values[0], identity, "institution")
    target = _safe_text(identity_values[1], identity, "target")
    fee_text, fee_amount = _fee(
        _safe_text(_detail_values(rows[3])[0], identity, "fee"), identity
    )
    period_values = _detail_values(rows[4])
    apply_start, apply_end, apply_period = _datetime_range(
        _safe_text(period_values[0], identity, "application period"), identity
    )
    start, end, period = _date_range(
        _safe_text(period_values[1], identity, "education period"),
        identity,
        "education period",
    )
    venue = _safe_text(_detail_values(rows[5])[1], identity, "venue")
    capacity_values = _detail_values(rows[6])
    capacity_total = _count(
        _safe_text(capacity_values[0], identity, "capacity"),
        _CAPACITY_RE,
        identity,
        "detail capacity",
    )
    capacity_wait_total = _count(
        _safe_text(capacity_values[1], identity, "wait capacity"),
        _CAPACITY_RE,
        identity,
        "detail wait capacity",
    )
    if (
        category != _clean(listed.get("category"))
        or title != _clean(listed.get("title"))
        or fee_amount != int(listed.get("fee_amount") or 0)
        or (apply_start, apply_end, apply_period)
        != (
            _clean(listed.get("apply_start")),
            _clean(listed.get("apply_end")),
            _clean(listed.get("apply_period")),
        )
        or (start, end, period)
        != (listed.get("start"), listed.get("end"), _clean(listed.get("period")))
        or capacity_total != int(listed.get("capacity_total") or 0)
    ):
        raise JangheungContractError(f"course {identity}: list/detail safe fields mismatch")
    controls: list[tuple[str, str]] = []
    for link in soup.select("#content a[href]"):
        parsed = urlparse(urljoin(JANGHEUNG_URL, _clean(link.get("href"))))
        query = parse_qs(parsed.query)
        if "lecture_idx" in query or str(_single_query(query, "mode")).startswith("reserve_form"):
            if tuple(link.get("class") or ()) != ("s_bt", "bt1"):
                raise JangheungContractError(f"course {identity}: application class changed")
            controls.append(_application_link(link.get("href")))
    controls = sorted(set(controls))
    status = _clean(listed.get("status"))
    expected_application = _clean(listed.get("application_url"))
    if status == "OPEN":
        if controls != [(identity, expected_application)] or not expected_application:
            raise JangheungContractError(
                f"course {identity}: detail/list application control mismatch"
            )
    elif controls or expected_application:
        raise JangheungContractError(
            f"course {identity}: inactive detail exposes an application control"
        )
    list_buttons = soup.select("#content div.list_btn ul.clear > li > a#btn_list")
    if len(list_buttons) != 1:
        raise JangheungContractError(f"course {identity}: detail list return changed")
    button = list_buttons[0]
    parsed_button = _parse_owned_url(button.get("href"))
    button_query = parse_qs(parsed_button.query, keep_blank_values=True)
    if (
        tuple(button.get("class") or ()) != ("m_bt", "bt6")
        or _clean(button.get("title")) != "목록"
        or _clean(button.get_text(" ", strip=True)) != "목록"
        or parsed_button.path != JANGHEUNG_PATH
        or button_query != {"page": [""]}
    ):
        raise JangheungContractError(f"course {identity}: detail ownership changed")
    branch = _branch_name(institution)
    visible_control = status == "OPEN"
    return {
        "provider": JANGHEUNG_PROVIDER,
        "provider_course_id": f"{JANGHEUNG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": _branch_code(institution),
        "preserve_branch": True,
        "category": category,
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": expected_application if visible_control else "",
        "application_type": "ONLINE_RESERVATION" if visible_control else "INFO_ONLY",
        "application_method": "온라인" if visible_control else "",
        "application_methods": ["온라인"] if visible_control else [],
        "reservation_available": visible_control,
        "status": status,
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
        "collection_type": JANGHEUNG_PARSER,
        "municipality_code": JANGHEUNG_MUNICIPALITY_CODE,
        "municipality_full_name": JANGHEUNG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_page": int(listed.get("source_page") or 0),
            "source_row_number": int(listed.get("row_number") or 0),
            "source_status": _clean(listed.get("source_status")),
            "source_category": category,
            "source_period": period,
            "source_apply_period": apply_period,
            "source_fee": _clean(listed.get("fee")),
            "source_capacity_current": int(listed.get("capacity_current") or 0),
            "source_wait_current": int(listed.get("wait_current") or 0),
            "source_capacity_total": int(listed.get("capacity_total") or 0),
            "source_institution": institution,
            "detail_target": target,
            "detail_venue": venue,
            "detail_capacity_wait_total": capacity_wait_total,
            "detail_verified": True,
            "visible_application_control_present": visible_control,
            "application_control_contract": (
                "identity_bound_login_route" if visible_control else "verified_no_control"
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
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url"}
        }
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
        "row_number_duplicate_count": 0,
        "semantic_duplicate_group_count": 0,
        "historical_semantic_duplicate_group_count": 0,
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
        "municipality_code": JANGHEUNG_MUNICIPALITY_CODE,
        "municipality_name": JANGHEUNG_MUNICIPALITY_NAME,
        "canonical_url": JANGHEUNG_URL,
        "ownership_scope": JANGHEUNG_OWNERSHIP_SCOPE,
        "candidate_audit": {
            key: dict(value) for key, value in JANGHEUNG_CANDIDATE_AUDIT.items()
        },
        "discovery_audit": dict(JANGHEUNG_DISCOVERY_AUDIT),
        "duplicate_provider_aliases": [JANGHEUNG_DUPLICATE_LANDING_PROVIDER],
        "municipality_coverage": [JANGHEUNG_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(JANGHEUNG_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
    }


def collect_jangheung_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = JANGHEUNG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future Jangheung lifelong snapshot."""

    meta = _base_meta()
    if not is_jangheung_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Jangheung lifelong catalogue owner"
        )
        return [], JANGHEUNG_PARSER, meta
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
        return [], JANGHEUNG_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], JANGHEUNG_PARSER, meta
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, JANGHEUNG_MAX_WORKERS)
    meta["network_concurrency"] = workers

    def fetch_list(
        page: int, *, source_pages: Optional[int] = None, sentinel: bool = False
    ) -> _ListPage:
        soup = _fetch_soup(
            jangheung_list_url(page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(
            soup, page, source_pages=source_pages, sentinel=sentinel
        )

    try:
        first = fetch_list(1)
        meta["list_requests"] = 1
        meta["pages"] = 1
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"page 1: {type(exc).__name__}: {_clean(exc)}"
        )
        return [], JANGHEUNG_PARSER, meta
    source_pages = first.source_pages
    required = source_pages + 3
    meta.update(
        {
            "source_total": first.total,
            "declared_source_rows": first.total,
            "declared_data_pages": source_pages,
            "required_list_requests": required,
            "sentinel_page": source_pages + 1,
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
        return [], JANGHEUNG_PARSER, meta

    pages: dict[int, _ListPage] = {1: first}
    errors: list[str] = []
    for page in range(2, source_pages + 1):
        try:
            pages[page] = fetch_list(page, source_pages=source_pages)
            meta["list_requests"] += 1
            meta["pages"] += 1
        except Exception as exc:
            errors.append(f"page {page}: {type(exc).__name__}: {_clean(exc)}")
    sentinel: Optional[_ListPage] = None
    first_recheck: Optional[_ListPage] = None
    last_recheck: Optional[_ListPage] = None
    for kind, page, is_sentinel in (
        ("sentinel", source_pages + 1, True),
        ("first_recheck", 1, False),
        ("last_recheck", source_pages, False),
    ):
        try:
            parsed = fetch_list(
                page, source_pages=source_pages, sentinel=is_sentinel
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            if kind == "sentinel":
                sentinel = parsed
            elif kind == "first_recheck":
                first_recheck = parsed
            else:
                last_recheck = parsed
        except Exception as exc:
            errors.append(f"{kind} page {page}: {type(exc).__name__}: {_clean(exc)}")

    for page, parsed in pages.items():
        if parsed.total != first.total or parsed.source_pages != source_pages:
            errors.append(f"page {page}: total/page boundary changed")
        if first.total > 0 and not parsed.rows:
            errors.append(f"page {page}: declared data page is empty")
        if parsed.empty_marker != (not parsed.rows):
            errors.append(f"page {page}: row/empty marker mismatch")
    if sentinel is None:
        errors.append("immediate post-last sentinel response missing")
    elif (
        sentinel.total != first.total
        or sentinel.source_pages != source_pages
        or sentinel.rows
        or not sentinel.empty_marker
    ):
        errors.append("immediate post-last sentinel is not stable empty")
    else:
        meta["sentinel_requests"] = 1
    if first_recheck is None or last_recheck is None:
        errors.append("first/last stability recheck response missing")
    else:
        meta["stability_rechecks"] = 2
        if (
            first_recheck.total != first.total
            or first_recheck.source_pages != source_pages
            or _page_signature(first_recheck.rows) != _page_signature(first.rows)
        ):
            errors.append("first-page stability recheck changed")
        last = pages.get(source_pages)
        if (
            last is None
            or last_recheck.total != first.total
            or last_recheck.source_pages != source_pages
            or _page_signature(last_recheck.rows) != _page_signature(last.rows)
        ):
            errors.append("last-page stability recheck changed")

    listed = [row for page in range(1, source_pages + 1) for row in pages.get(page, _ListPage(page, first.total, source_pages, (), True)).rows]
    identities = [_clean(row.get("identity")) for row in listed]
    raw_urls = [_clean(row.get("raw_url")) for row in listed]
    row_numbers = [int(row.get("row_number") or 0) for row in listed]
    identity_duplicates = len(identities) - len(set(identities))
    raw_url_duplicates = len(raw_urls) - len(set(raw_urls))
    row_number_duplicates = len(row_numbers) - len(set(row_numbers))
    if len(listed) != first.total:
        errors.append(f"complete source row count {len(listed)} != {first.total}")
    if sorted(row_numbers) != list(range(1, first.total + 1)):
        errors.append("official descending row numbers do not reconcile the total")
    if identity_duplicates:
        errors.append(f"{identity_duplicates} duplicate official identities")
    if raw_url_duplicates:
        errors.append(f"{raw_url_duplicates} duplicate canonical detail URLs")
    if row_number_duplicates:
        errors.append(f"{row_number_duplicates} duplicate official row numbers")
    current_listed = [row for row in listed if row["end"] >= cutoff]
    historical_listed = [row for row in listed if row["end"] < cutoff]
    historical_semantic = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("category")),
        )
        for row in historical_listed
    )
    historical_semantic_groups = sum(value > 1 for value in historical_semantic.values())
    list_complete = bool(
        not errors
        and len(pages) == source_pages
        and meta["list_requests"] == required
        and meta["sentinel_requests"] == 1
        and meta["stability_rechecks"] == 2
        and len(listed) == first.total
    )
    meta.update(
        {
            "data_pages": len(pages),
            "page_counts": {page: len(parsed.rows) for page, parsed in pages.items()},
            "source_rows": len(listed),
            "current_source_count": len(current_listed),
            "expired_count": len(historical_listed),
            "identity_duplicate_count": identity_duplicates,
            "raw_url_duplicate_count": raw_url_duplicates,
            "row_number_duplicate_count": row_number_duplicates,
            "historical_semantic_duplicate_group_count": historical_semantic_groups,
            "source_application_control_count": sum(
                bool(row.get("application_url")) for row in listed
            ),
            "source_status_counts": dict(
                Counter(_clean(row.get("source_status")) for row in listed)
            ),
            "source_category_counts": dict(
                Counter(_clean(row.get("category")) for row in listed)
            ),
            "current_normalized_status_counts": dict(
                Counter(_clean(row.get("status")) for row in current_listed)
            ),
            "pagination_detected": source_pages > 1,
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], JANGHEUNG_PARSER, meta
    if len(current_listed) > detail_limit:
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": (
                    f"detail_limit cap allows {detail_limit} of "
                    f"{len(current_listed)} required current details"
                ),
            }
        )
        return [], JANGHEUNG_PARSER, meta

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
                    raise JangheungContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
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
            _normalized(row.get("raw_fields", {}).get("source_institution")),
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
                    "dedupe changed official identity cardinality "
                    f"{len(ordered)} to {len(result)}"
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
            "current_branch_names": sorted(
                {_clean(row.get("branch")) for row in result if _clean(row.get("branch"))}
            ),
            "institution_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_institution"))
                    for row in result
                )
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
                "the complete official Jangheung lifelong catalogue has no current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, JANGHEUNG_PARSER, meta


collect = collect_jangheung_education


__all__ = [
    "JANGHEUNG_CANDIDATE_AUDIT",
    "JANGHEUNG_CANDIDATE_ID",
    "JANGHEUNG_DISCOVERY_AUDIT",
    "JANGHEUNG_DUPLICATE_LANDING_PROVIDER",
    "JANGHEUNG_MUNICIPALITY_CODE",
    "JANGHEUNG_MUNICIPALITY_NAME",
    "JANGHEUNG_PARSER",
    "JANGHEUNG_PROVIDER",
    "JANGHEUNG_URL",
    "collect",
    "collect_jangheung_education",
    "is_jangheung_candidate_alias",
    "is_jangheung_education_target",
    "is_target",
    "jangheung_application_url",
    "jangheung_detail_url",
    "jangheung_list_url",
]
