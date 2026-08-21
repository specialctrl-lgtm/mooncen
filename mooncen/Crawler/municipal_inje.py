"""Fail-closed Inje-gun official lifelong-education collector.

The official site exposes two disjoint current/historical catalogues under one
course identity namespace: courses operated by the Inje Lifelong Learning
Center and courses operated by other registered lifelong-education
institutions.  Both lists are owned by the existing municipal provider.  A
second generated provider currently points at the institution list but also
dispatches through the same legacy two-list collector, so it is a duplicate
alias rather than another owner.

This collector derives every page from each declared total, requires the
immediate empty/reset sentinel, and rechecks the first and last data pages.
Every current/future row is then verified against its course detail and its
visible public application control.  Contact, instructor, attachment,
free-form content and applicant-form values are deliberately never read from
their detail cells or persisted.
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

from utils.outbound_http import SafeSession


INJE_PROVIDER = "MUNI_WWW_INJE_GO_KR_44A2D640"
INJE_DUPLICATE_EDU_PROVIDER = "MUNI_LIFELONG_INJE_GO_KR_8A322659"
INJE_LANDING_CANDIDATE_ID = "MUNI_IR_CFF41CBF5F16"
INJE_EDUCATION_SUPPORT_CANDIDATE_ID = "MUNI_IR_29ACF23F1481"
INJE_EXISTING_OWNER_AUDIT_ID = "EXISTING_MUNI_WWW_INJE_GO_KR_44A2D640"
INJE_DUPLICATE_OWNER_AUDIT_ID = "EXISTING_MUNI_LIFELONG_INJE_GO_KR_8A322659"
INJE_EDU_BOARD_AUDIT_ID = "OFFICIAL_INJE_EDU_EDITORIAL_BOARD"
INJE_ARCHIVE_AUDIT_ID = "OFFICIAL_INJE_PAST_PROGRAM_ARCHIVE"
INJE_FACILITIES_AUDIT_ID = "OFFICIAL_INJE_FACILITY_METADATA"

INJE_HOST = "lifelong.inje.go.kr"
INJE_PORTAL_HOST = "www.inje.go.kr"
INJE_SUPPORT_HOST = "gwijed.gwe.go.kr"
INJE_CENTER_PATH = "/lct/course/list"
INJE_INSTITUTION_PATH = "/lct/edu/list"
INJE_DETAIL_PATH = "/lct/course/view"
INJE_PAYMENT_PATH = "/payment/nicepay/payRequest"
INJE_CENTER_URL = f"https://{INJE_HOST}{INJE_CENTER_PATH}"
INJE_INSTITUTION_URL = f"https://{INJE_HOST}{INJE_INSTITUTION_PATH}"
INJE_CANONICAL_URL = INJE_CENTER_URL
INJE_LANDING_URL = "https://www.inje.go.kr/portal/participation"
INJE_EDUCATION_SUPPORT_URL = (
    "https://gwijed.gwe.go.kr/boardCnts/list.do?boardID=2080&m=0101"
)
INJE_EDU_BOARD_URL = f"https://{INJE_HOST}/brd/post/edu/list"
INJE_ARCHIVE_URL = f"https://{INJE_HOST}/lct/program/list"
INJE_FACILITIES_URL = f"https://{INJE_HOST}/facilities/list"
INJE_MUNICIPALITY_CODE = "5181000000"
INJE_MUNICIPALITY_NAME = "강원특별자치도 인제군"
INJE_CENTER_BRANCH = "인제군평생학습센터"
INJE_PAGE_SIZE = 10
INJE_MAX_WORKERS = 8
INJE_FETCH_ATTEMPTS = 3
INJE_RETRY_BACKOFF_SECONDS = 0.2
INJE_MAX_HTML_BYTES = 8_000_000
INJE_DOCUMENT_TITLE = "인제군 평생학습센터"
INJE_PARSER = (
    "inje_official_lifelong_center+institutions_all_pages+reset_sentinels+"
    "stable_boundaries+all_current_details+course_bound_login_controls+"
    "institution_branches+current_semantic_duplicate_zero+pii_allowlist"
)
INJE_OWNERSHIP_SCOPE = (
    "inje_official_lifelong_center_and_registered_institution_catalogues"
)

INJE_SCOPE_PATHS: Mapping[str, str] = {
    "center": INJE_CENTER_PATH,
    "institution": INJE_INSTITUTION_PATH,
}
INJE_CATEGORIES = frozenset(
    {
        "문화/예술",
        "IT/컴퓨터",
        "취업/자격증",
        "인문/시민교육",
        "외국어교육",
        "체육",
        "기타",
    }
)
INJE_AGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "전체"),
    ("1", "어린이"),
    ("2", "청소년"),
    ("3", "성인"),
    ("4", "어르신"),
)
INJE_PAY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("", "전체"),
    ("paid", "유료"),
    ("free", "무료"),
)
INJE_STATUS_MAP: Mapping[str, str] = {
    "신청중": "OPEN",
    "신청마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "폐강": "CANCELLED",
}

INJE_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    INJE_EXISTING_OWNER_AUDIT_ID: {
        "decision": "include_existing_provider_as_complete_two_scope_owner",
        "provider": INJE_PROVIDER,
        "url": INJE_CENTER_URL,
        "second_scope_url": INJE_INSTITUTION_URL,
        "owner": INJE_PROVIDER,
    },
    INJE_DUPLICATE_OWNER_AUDIT_ID: {
        "decision": "exclude_duplicate_provider_alias_already_collected_by_owner",
        "provider": INJE_DUPLICATE_EDU_PROVIDER,
        "url": INJE_INSTITUTION_URL,
        "owner": INJE_PROVIDER,
    },
    INJE_LANDING_CANDIDATE_ID: {
        "decision": "exclude_discovery_portal_free_board_superseded_by_lifelong_lists",
        "provider": INJE_PROVIDER,
        "url": INJE_LANDING_URL,
        "owner": INJE_PROVIDER,
    },
    INJE_EDUCATION_SUPPORT_CANDIDATE_ID: {
        "decision": "exclude_separate_education_support_notice_board_not_course_booking",
        "provider": "MUNI_GWIJED_GWE_GO_KR_EC2CD684",
        "url": INJE_EDUCATION_SUPPORT_URL,
        "owner": "separate_education_support_notice_service",
    },
    INJE_EDU_BOARD_AUDIT_ID: {
        "decision": "exclude_stale_editorial_course_board_without_booking_contract",
        "provider": INJE_PROVIDER,
        "url": INJE_EDU_BOARD_URL,
        "owner": INJE_PROVIDER,
    },
    INJE_ARCHIVE_AUDIT_ID: {
        "decision": "exclude_explicit_past_course_archive",
        "provider": INJE_PROVIDER,
        "url": INJE_ARCHIVE_URL,
        "owner": INJE_PROVIDER,
    },
    INJE_FACILITIES_AUDIT_ID: {
        "decision": "exclude_facility_metadata_without_course_rows",
        "provider": INJE_PROVIDER,
        "url": INJE_FACILITIES_URL,
        "owner": INJE_PROVIDER,
    },
}

INJE_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_url": INJE_CANONICAL_URL,
    "center_source_rows": 766,
    "institution_source_rows": 135,
    "source_rows": 901,
    "center_data_pages": 77,
    "institution_data_pages": 14,
    "data_pages": 91,
    "required_list_requests": 97,
    "current_or_future_rows": 35,
    "detail_pages_verified": 35,
    "current_scope_counts": {"center": 33, "institution": 2},
    "current_source_status_counts": {
        "신청중": 5,
        "신청마감": 19,
        "교육중": 11,
    },
    "normalized_status_counts": {"OPEN": 5, "CLOSED": 30},
    "current_institution_counts": {
        "인제군평생학습센터": 33,
        "인제 천리길": 1,
        "인제군 문화교육과 교육협력": 1,
    },
    "visible_public_application_controls": 5,
    "current_semantic_duplicate_count": 0,
    "identity_duplicate_count": 0,
    "historical_semantic_duplicate_group_count": 7,
    "historical_reversed_education_period_count": 6,
    "historical_reversed_application_period_count": 1,
    "conclusion": "two_disjoint_lists_roll_up_to_existing_single_owner",
}

INJE_PII_FIELDS_DISCARDED = (
    "연락처",
    "강사명",
    "첨부파일",
    "내용",
    "applicant_form_values",
    "member_phone",
    "member_description",
    "source_html",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


class InjeContractError(ValueError):
    """Raised when the verified official catalogue contract changes."""


@dataclass(frozen=True)
class _ListPage:
    scope: str
    requested_page: int
    displayed_page: int
    total: int
    data_last: int
    rows: tuple[dict[str, Any], ...]
    empty_marker: bool


_SPACE_RE = re.compile(r"\s+")
_POSITIVE_ID_RE = re.compile(r"^[1-9]\d*$")
_DATE_PAIR_RE = re.compile(
    r"^(?P<start>20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(?P<end>20\d{2}-\d{2}-\d{2})$"
)
_TOTAL_RE = re.compile(r"^총\s+(?P<total>[\d,]+)\s+건의\s+강좌가\s+있습니다\.$")
_PAGE_HANDLER_RE = re.compile(
    r"^javascript:admin\.pageMove\((?P<page>[1-9]\d*),\s*'#list-form'\);$"
)
_LIST_FIELD_LABELS = (
    "교육기간",
    "신청기간",
    "교육대상",
    "접수상태",
    "수강료/재료비",
    "모집인원",
)
_DETAIL_LABELS = (
    "교육기간",
    "교육대상",
    "교육장소",
    "교육일시",
    "연락처",
    "강사명",
    "온라인 결제여부",
    "첨부파일",
    "내용",
)
_SAFE_DETAIL_LABELS = frozenset(
    {"교육기간", "교육대상", "교육장소", "교육일시", "온라인 결제여부"}
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "source_scope",
        "source_page",
        "source_status",
        "source_institution",
        "source_category",
        "source_period",
        "source_apply_period",
        "source_target",
        "source_fee",
        "source_material_fee",
        "source_capacity",
        "detail_schedule",
        "detail_venue",
        "online_payment",
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
        "attachments",
        "attachment_urls",
        "description_html",
        "detail_description",
        "source_html",
        "raw_html",
        "applicant_name",
        "applicant_phone",
    }
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).lower()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _safe_port(parsed: Any) -> Optional[int]:
    try:
        return parsed.port
    except ValueError:
        return -1


def is_inje_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != INJE_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").rstrip(".").lower() == INJE_HOST
        and _safe_port(parsed) is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == INJE_CENTER_PATH
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_inje_education_target


def is_inje_candidate_alias(target: Any) -> bool:
    candidate = _clean(_target_value(target, "candidate_id"))
    provider = _clean(_target_value(target, "provider"))
    url = _clean(_target_value(target, "url"))
    return bool(
        candidate in INJE_CANDIDATE_AUDIT
        or provider == INJE_DUPLICATE_EDU_PROVIDER
        or url
        in {
            INJE_INSTITUTION_URL,
            INJE_LANDING_URL,
            INJE_EDUCATION_SUPPORT_URL,
            INJE_EDU_BOARD_URL,
            INJE_ARCHIVE_URL,
            INJE_FACILITIES_URL,
        }
    )


def inje_list_url(scope: str, page: int) -> str:
    key = _clean(scope)
    if key not in INJE_SCOPE_PATHS:
        raise ValueError("unknown Inje catalogue scope")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query = urlencode((("page.page", str(page)),))
    return f"https://{INJE_HOST}{INJE_SCOPE_PATHS[key]}?{query}"


def inje_detail_url(identity: str) -> str:
    value = _clean(identity)
    if _POSITIVE_ID_RE.fullmatch(value) is None:
        raise ValueError("course identity must be a positive integer")
    query = urlencode((("courseSeq", value),))
    return f"https://{INJE_HOST}{INJE_DETAIL_PATH}?{query}"


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def configure_inje_verified_session(session: requests.Session) -> requests.Session:
    """Apply source headers; SafeSession supplies the host-scoped CA profile."""

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": INJE_CENTER_URL,
        }
    )
    return session


def _default_session_factory() -> requests.Session:
    return configure_inje_verified_session(SafeSession())


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    status = int(getattr(response, "status_code", 0))
    if status != 200:
        raise InjeContractError(f"unexpected HTTP status {status}")
    if getattr(response, "headers", {}).get("Location"):
        raise InjeContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise InjeContractError("empty HTTP response")
    if len(content) > INJE_MAX_HTML_BYTES:
        raise InjeContractError("HTTP response exceeded HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        if len(value) > INJE_MAX_HTML_BYTES:
            raise InjeContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml", from_encoding="utf-8")
    if isinstance(value, str):
        if len(value.encode("utf-8")) > INJE_MAX_HTML_BYTES:
            raise InjeContractError("HTML fixture exceeded byte cap")
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher returned neither HTML nor response")
    if len(content) > INJE_MAX_HTML_BYTES:
        raise InjeContractError("HTTP response exceeded HTML byte cap")
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
    for attempt in range(INJE_FETCH_ATTEMPTS):
        session: Any = None
        try:
            session = session_factory()
            return _coerce_soup(fetcher(session, url, timeout))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < INJE_FETCH_ATTEMPTS:
                time.sleep(INJE_RETRY_BACKOFF_SECONDS * (attempt + 1))
        finally:
            _close_quietly(session)
    assert last_error is not None
    raise last_error


def _document_title(soup: BeautifulSoup, label: str) -> None:
    titles = soup.select("head > title")
    if len(titles) != 1 or _clean(titles[0].get_text(" ", strip=True)) != INJE_DOCUMENT_TITLE:
        raise InjeContractError(f"{label}: official page title changed")


def _single_input(
    form: Any,
    *,
    selector: str,
    expected: Optional[str] = None,
    nonempty: bool = False,
    label: str,
) -> Any:
    nodes = form.select(selector)
    if len(nodes) != 1:
        raise InjeContractError(f"{label} changed")
    value = _clean(nodes[0].get("value"))
    if expected is not None and value != expected:
        raise InjeContractError(f"{label} changed")
    if nonempty and not value:
        raise InjeContractError(f"{label} changed")
    return nodes[0]


def _select_options(form: Any, selector: str, label: str) -> tuple[tuple[str, str], ...]:
    nodes = form.select(selector)
    if len(nodes) != 1:
        raise InjeContractError(f"{label} changed")
    return tuple(
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in nodes[0].find_all("option", recursive=False)
    )


def _validate_list_form(
    soup: BeautifulSoup,
    scope: str,
    requested_page: int,
    *,
    sentinel: bool,
) -> int:
    forms = soup.select("form#list-form")
    if len(forms) != 1:
        raise InjeContractError(f"{scope} page {requested_page}: list form changed")
    form = forms[0]
    action = urlparse(urljoin(INJE_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("name")) != "search"
        or action.scheme != "https"
        or action.hostname != INJE_HOST
        or action.path != INJE_SCOPE_PATHS[scope]
        or action.query
        or action.fragment
    ):
        raise InjeContractError(f"{scope} page {requested_page}: list ownership changed")
    _single_input(
        form,
        selector='input[type="hidden"][name="ptSignature"]',
        nonempty=True,
        label=f"{scope} page {requested_page}: signature field",
    )
    paging = _single_input(
        form,
        selector='input[type="hidden"]#paging-page[name="page.page"]',
        expected="1" if sentinel else str(requested_page),
        label=f"{scope} page {requested_page}: paging field",
    )
    if tuple(paging.get("class") or ()) != ("search-elements",):
        raise InjeContractError(f"{scope} page {requested_page}: paging class changed")
    _single_input(
        form,
        selector='input[type="hidden"]#teach-select',
        expected="",
        label=f"{scope} page {requested_page}: teach selection",
    )
    _single_input(
        form,
        selector='input[type="checkbox"]#status-check[name="statuscheck"]',
        expected="",
        label=f"{scope} page {requested_page}: status checkbox",
    )
    _single_input(
        form,
        selector='input[type="hidden"]#status-type[name="statusType"]',
        expected="",
        label=f"{scope} page {requested_page}: status field",
    )
    _single_input(
        form,
        selector='input[type="hidden"]#key-field[name="keyField"]',
        expected="COURSE_NAME",
        label=f"{scope} page {requested_page}: search key",
    )
    _single_input(
        form,
        selector='input[type="text"]#search-word[name="searchWord"]',
        expected="",
        label=f"{scope} page {requested_page}: search word",
    )
    if _select_options(form, 'select#age-type[name="ageType"]', "age taxonomy") != INJE_AGE_OPTIONS:
        raise InjeContractError("age taxonomy changed")
    if _select_options(form, 'select#pay-select[name="paySelect"]', "fee taxonomy") != INJE_PAY_OPTIONS:
        raise InjeContractError("fee taxonomy changed")
    teach = form.select('select#teach-type[name="teachType"]')
    if len(teach) != 1 or teach[0].find_all("option", recursive=False):
        raise InjeContractError("course-category dynamic taxonomy changed")
    facilities = form.select('select#facilities-type[name="facilitiesType"]')
    facility_hidden = form.select('input[type="hidden"]#facilities-select')
    if scope == "center":
        if facilities or facility_hidden:
            raise InjeContractError("center scope gained an institution filter")
    else:
        if len(facilities) != 1 or facilities[0].find_all("option", recursive=False):
            raise InjeContractError("institution dynamic taxonomy changed")
        if len(facility_hidden) != 1 or _clean(facility_hidden[0].get("value")):
            raise InjeContractError("institution selection field changed")
    return int(_clean(paging.get("value")))


def _parse_total_and_paging(
    soup: BeautifulSoup,
    scope: str,
    requested_page: int,
    displayed_page: int,
) -> tuple[int, int]:
    headings = soup.select("div.tblTopArea > h4")
    if len(headings) != 1:
        raise InjeContractError(f"{scope} page {requested_page}: total heading changed")
    match = _TOTAL_RE.fullmatch(_clean(headings[0].get_text(" ", strip=True)))
    if match is None or len(headings[0].select(":scope > span")) != 1:
        raise InjeContractError(f"{scope} page {requested_page}: declared total changed")
    total = int(match.group("total").replace(",", ""))
    last = max(1, math.ceil(total / INJE_PAGE_SIZE))
    pagers = soup.select("div.btnArea.mt40 > ul.paging")
    if len(pagers) != 1:
        raise InjeContractError(f"{scope} page {requested_page}: pagination changed")
    active = pagers[0].select(":scope > li > a.on")
    if len(active) != 1:
        raise InjeContractError(f"{scope} page {requested_page}: active page changed")
    handler = _PAGE_HANDLER_RE.fullmatch(_clean(active[0].get("href")))
    if (
        handler is None
        or int(handler.group("page")) != displayed_page
        or _clean(active[0].get_text(" ", strip=True)) != str(displayed_page)
    ):
        raise InjeContractError(f"{scope} page {requested_page}: active page mismatch")
    if displayed_page == 1 and last > 1:
        final = pagers[0].select(":scope > li > a.last")
        if len(final) != 1:
            raise InjeContractError(f"{scope} page {requested_page}: last-page control changed")
        final_match = _PAGE_HANDLER_RE.fullmatch(_clean(final[0].get("href")))
        if final_match is None or int(final_match.group("page")) != last:
            raise InjeContractError(f"{scope} page {requested_page}: last page mismatch")
    return total, last


def _parse_date_pair(
    value: Any, identity: str, label: str
) -> tuple[date, date, str, bool]:
    text = _clean(value)
    match = _DATE_PAIR_RE.fullmatch(text)
    if match is None:
        raise InjeContractError(f"course {identity}: {label} changed")
    start = date.fromisoformat(match.group("start"))
    end = date.fromisoformat(match.group("end"))
    return start, end, text, start > end


def _extract_title(node: Any, identity: str) -> tuple[str, str]:
    clone = BeautifulSoup(str(node), "lxml").select_one(".eduTitle")
    if clone is None:
        raise InjeContractError(f"course {identity}: title clone failed")
    categories = clone.select(":scope > span")
    if len(categories) != 1:
        raise InjeContractError(f"course {identity}: category marker changed")
    category = _clean(categories[0].get_text(" ", strip=True)).strip("[]")
    categories[0].decompose()
    title = _clean(clone.get_text(" ", strip=True))
    if not title or category not in INJE_CATEGORIES:
        raise InjeContractError(f"course {identity}: title/category changed")
    return title, category


def _parse_card(anchor: Any, scope: str, page: int) -> dict[str, Any]:
    if _clean(anchor.get("onclick")) or _clean(anchor.get("target")):
        raise InjeContractError(f"{scope} page {page}: course anchor gained a handler")
    parsed = urlparse(urljoin(INJE_CANONICAL_URL, _clean(anchor.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identities = query.get("courseSeq", [])
    if (
        parsed.scheme != "https"
        or parsed.hostname != INJE_HOST
        or _safe_port(parsed) is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != INJE_DETAIL_PATH
        or set(query) != {"courseSeq"}
        or len(identities) != 1
        or _POSITIVE_ID_RE.fullmatch(identities[0]) is None
        or parsed.fragment
    ):
        raise InjeContractError(f"{scope} page {page}: course identity link changed")
    identity = identities[0]
    centers = anchor.select(":scope > div.eduCenter")
    titles = anchor.select(":scope > div.eduTitle")
    if len(centers) != 1 or len(titles) != 1:
        raise InjeContractError(f"course {identity}: card heading changed")
    institution = _clean(centers[0].get_text(" ", strip=True))
    if (
        not institution
        or len(institution) > 100
        or _PHONE_RE.search(institution)
        or _EMAIL_RE.search(institution)
        or (scope == "center") != (institution == INJE_CENTER_BRANCH)
    ):
        raise InjeContractError(f"course {identity}: source institution changed")
    title, category = _extract_title(titles[0], identity)
    paragraphs = anchor.find_all("p", recursive=False)
    if len(paragraphs) != len(_LIST_FIELD_LABELS):
        raise InjeContractError(f"course {identity}: card field count changed")
    fields: dict[str, str] = {}
    for expected, paragraph in zip(_LIST_FIELD_LABELS, paragraphs):
        text = _clean(paragraph.get_text(" ", strip=True))
        marker = f"{expected}:"
        if not text.startswith(marker):
            raise InjeContractError(f"course {identity}: card field order changed")
        fields[expected] = _clean(text[len(marker) :])
    source_status = fields["접수상태"]
    status_nodes = paragraphs[3].select(":scope > span")
    if len(status_nodes) != 1 or _clean(status_nodes[0].get_text(" ", strip=True)) != source_status:
        raise InjeContractError(f"course {identity}: reception marker changed")
    classes = tuple(status_nodes[0].get("class") or ())
    if source_status not in INJE_STATUS_MAP or (
        (source_status == "신청중" and classes != ("state1",))
        or (source_status != "신청중" and classes)
    ):
        raise InjeContractError(f"course {identity}: reception state changed")
    start, end, period, period_reversed = _parse_date_pair(
        fields["교육기간"], identity, "education period"
    )
    _apply_start, _apply_end, apply_period, apply_period_reversed = _parse_date_pair(
        fields["신청기간"], identity, "application period"
    )
    target = fields["교육대상"]
    fee_parts = tuple(_clean(value) for value in fields["수강료/재료비"].split("/"))
    if (
        len(fee_parts) != 2
        or len(target) > 100
        or len(fields["수강료/재료비"]) > 120
        or len(fields["모집인원"]) > 80
    ):
        raise InjeContractError(f"course {identity}: card value contract changed")
    return {
        "identity": identity,
        "source_scope": scope,
        "source_page": page,
        "title": title,
        "category": category,
        "institution": institution,
        "source_status": source_status,
        "status": INJE_STATUS_MAP[source_status],
        "start": start,
        "end": end,
        "period": period,
        "education_period_reversed": period_reversed,
        "apply_period": apply_period,
        "application_period_reversed": apply_period_reversed,
        "target": target,
        "fee": fee_parts[0],
        "material_fee": fee_parts[1],
        "source_fee": fields["수강료/재료비"],
        "capacity": fields["모집인원"],
        "raw_url": inje_detail_url(identity),
    }


def _parse_list_page(
    soup: BeautifulSoup,
    scope: str,
    page: int,
    *,
    sentinel: bool = False,
) -> _ListPage:
    _document_title(soup, f"{scope} page {page}")
    displayed_page = _validate_list_form(soup, scope, page, sentinel=sentinel)
    total, last = _parse_total_and_paging(soup, scope, page, displayed_page)
    roots = soup.select("div.eduList2")
    if len(roots) != 1:
        raise InjeContractError(f"{scope} page {page}: course list changed")
    lists = roots[0].select(":scope > ul")
    if len(lists) != 1:
        raise InjeContractError(f"{scope} page {page}: course list body changed")
    items = lists[0].find_all("li", recursive=False)
    if len(items) != 1:
        raise InjeContractError(f"{scope} page {page}: course list grouping changed")
    anchors = items[0].find_all("a", recursive=False)
    data = [node for node in anchors if "courseSeq=" in _clean(node.get("href"))]
    if data:
        if len(data) != len(anchors):
            raise InjeContractError(f"{scope} page {page}: mixed data/empty anchors")
        rows = tuple(_parse_card(node, scope, page) for node in data)
        empty_marker = False
    else:
        if (
            len(anchors) != 1
            or _clean(anchors[0].get("href")) != "javascript:void(0);"
            or _clean(anchors[0].get("onclick"))
            or len(anchors[0].select(":scope > div.eduTitle")) != 1
            or _clean(anchors[0].get_text(" ", strip=True)) != "등록된 강좌가 없습니다."
            or anchors[0].select(":scope > div.eduCenter, :scope > p")
        ):
            raise InjeContractError(f"{scope} page {page}: empty marker changed")
        rows = ()
        empty_marker = True
    return _ListPage(scope, page, displayed_page, total, last, rows, empty_marker)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("category")),
            _clean(row.get("institution")),
            _clean(row.get("source_status")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
            _clean(row.get("target")),
            _clean(row.get("source_fee")),
            _clean(row.get("capacity")),
        )
        for row in rows
    )


def _safe_detail_value(fields: Mapping[str, list[Any]], name: str, identity: str) -> str:
    if name not in _SAFE_DETAIL_LABELS or len(fields.get(name, [])) != 1:
        raise InjeContractError(f"course {identity}: unsafe detail access")
    return _clean(fields[name][0].get_text(" ", strip=True))


def _capacity(value: str, identity: str) -> tuple[str, int]:
    match = re.fullmatch(r"(?P<count>[\d,]+)\s*명", _clean(value))
    if match is None:
        raise InjeContractError(f"course {identity}: current capacity changed")
    count = int(match.group("count").replace(",", ""))
    return f"{count}명", count


def _fee_amount(value: str) -> Optional[int]:
    text = _clean(value)
    if text == "무료":
        return 0
    if re.fullmatch(r"[\d,]+", text):
        return int(text.replace(",", ""))
    return None


def _branch_name(institution: str) -> str:
    return f"{INJE_MUNICIPALITY_NAME} / {institution}"


def _branch_code(institution: str) -> str:
    digest = hashlib.sha1(institution.encode("utf-8")).hexdigest()[:12]
    return f"inje:{digest}"


def _parse_detail(soup: BeautifulSoup, listed: Mapping[str, Any]) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    _document_title(soup, f"course {identity}")
    tables = soup.select("div.tblDetail-01 > table")
    if len(tables) != 1:
        raise InjeContractError(f"course {identity}: primary detail table changed")
    table = tables[0]
    headings = table.select(":scope > thead > tr > th")
    if len(headings) != 1:
        raise InjeContractError(f"course {identity}: detail heading changed")
    centers = headings[0].select(":scope > p.eduCenter")
    titles = headings[0].select(":scope > p.eduTitle")
    if len(centers) != 1 or len(titles) != 1:
        raise InjeContractError(f"course {identity}: detail identity heading changed")
    detail_institution = _clean(centers[0].get_text(" ", strip=True))
    detail_title, detail_category = _extract_title(titles[0], identity)
    if (
        detail_institution != _clean(listed.get("institution"))
        or detail_title != _clean(listed.get("title"))
        or detail_category != _clean(listed.get("category"))
    ):
        raise InjeContractError(f"course {identity}: list/detail identity mismatch")
    bodies = table.find_all("tbody", recursive=False)
    if len(bodies) != 1:
        raise InjeContractError(f"course {identity}: detail body changed")
    rows = bodies[0].find_all("tr", recursive=False)
    if len(rows) != len(_DETAIL_LABELS):
        raise InjeContractError(f"course {identity}: detail row count changed")
    fields: dict[str, list[Any]] = {}
    labels: list[str] = []
    for row in rows:
        headers = row.find_all("th", recursive=False)
        values = row.find_all("td", recursive=False)
        if len(headers) != 1 or not values:
            raise InjeContractError(f"course {identity}: detail schema changed")
        label = _clean(headers[0].get_text(" ", strip=True))
        labels.append(label)
        fields[label] = values
    if tuple(labels) != _DETAIL_LABELS:
        raise InjeContractError(f"course {identity}: detail schema changed")
    period = _safe_detail_value(fields, "교육기간", identity)
    target = _safe_detail_value(fields, "교육대상", identity)
    venue = _safe_detail_value(fields, "교육장소", identity)
    schedule = _safe_detail_value(fields, "교육일시", identity)
    online_payment = _safe_detail_value(fields, "온라인 결제여부", identity)
    if (
        period != _clean(listed.get("period"))
        or _normalized(target) != _normalized(listed.get("target"))
        or online_payment not in {"예", "아니오"}
        or not schedule
        or len(venue) > 300
        or len(schedule) > 300
        or _PHONE_RE.search(venue)
        or _EMAIL_RE.search(venue)
    ):
        raise InjeContractError(f"course {identity}: list/detail safe fields mismatch")
    forms = soup.select("form#pay-form")
    if len(forms) != 1:
        raise InjeContractError(f"course {identity}: course identity form changed")
    form = forms[0]
    action = urlparse(urljoin(INJE_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("name")) != "payForm"
        or action.scheme != "https"
        or action.hostname != INJE_HOST
        or action.path != INJE_PAYMENT_PATH
        or action.query
        or action.fragment
    ):
        raise InjeContractError(f"course {identity}: course identity form ownership changed")
    _single_input(
        form,
        selector='input[type="hidden"][name="ptSignature"]',
        nonempty=True,
        label=f"course {identity}: payment signature",
    )
    _single_input(
        form,
        selector='input[type="hidden"][name="courseSeq"]',
        expected=identity,
        label=f"course {identity}: payment identity",
    )
    areas = soup.select("div.btnArea.mt20")
    if len(areas) != 1:
        raise InjeContractError(f"course {identity}: detail button area changed")
    button_lists = areas[0].find_all("ul", recursive=False)
    if (
        len(button_lists) != 2
        or button_lists[0].get("class")
        or tuple(button_lists[1].get("class") or ()) != ("aRight",)
    ):
        raise InjeContractError(f"course {identity}: detail button grouping changed")
    controls = [
        node
        for node in button_lists[0].select(":scope > li > a")
        if _clean(node.get_text(" ", strip=True)) == "수강 신청"
    ]
    status = _clean(listed.get("status"))
    control = False
    application_url = ""
    if status == "OPEN":
        if (
            len(controls) != 1
            or tuple(controls[0].get("class") or ()) != ("course",)
            or _clean(controls[0].get("href")) != "javascript:noLogin()"
            or _clean(controls[0].get("onclick"))
            or _clean(controls[0].get("target"))
        ):
            raise InjeContractError(
                f"course {identity}: open course has no unique public application control"
            )
        scripts = "\n".join(node.get_text("\n") for node in soup.select("script"))
        if re.search(
            r"function\s+noLogin\(\)\s*\{.*?location\.href\s*=\s*"
            r"[\"']/main/login[\"'];",
            scripts,
            re.S,
        ) is None:
            raise InjeContractError(f"course {identity}: login application handler changed")
        control = True
        application_url = _clean(listed.get("raw_url"))
    elif controls:
        raise InjeContractError(
            f"course {identity}: inactive course exposes an application control"
        )
    capacity_text, capacity_total = _capacity(_clean(listed.get("capacity")), identity)
    institution = _clean(listed.get("institution"))
    branch = _branch_name(institution)
    return {
        "provider": INJE_PROVIDER,
        "provider_course_id": f"{INJE_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "description": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": _branch_code(institution),
        "preserve_branch": True,
        "category": _clean(listed.get("category")),
        "program_type": "교육",
        "raw_url": _clean(listed.get("raw_url")),
        "application_url": application_url,
        "application_type": "ONLINE_RESERVATION" if control else "INFO_ONLY",
        "application_method": "온라인" if control else "",
        "application_methods": ["온라인"] if control else [],
        "reservation_available": control,
        "status": status,
        "fee": _clean(listed.get("fee")),
        "fee_amount": _fee_amount(_clean(listed.get("fee"))),
        "material_fee": _clean(listed.get("material_fee")),
        "period": _clean(listed.get("period")),
        "start_date": listed["start"].isoformat(),
        "end_date": listed["end"].isoformat(),
        "apply_period": _clean(listed.get("apply_period")),
        "schedule_raw": schedule,
        "capacity": capacity_text,
        "capacity_current": None,
        "capacity_total": capacity_total,
        "target": _clean(listed.get("target")),
        "venue": venue,
        "venue_name": venue,
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": INJE_PARSER,
        "municipality_code": INJE_MUNICIPALITY_CODE,
        "municipality_full_name": INJE_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "source_scope": _clean(listed.get("source_scope")),
            "source_page": int(listed.get("source_page") or 0),
            "source_status": _clean(listed.get("source_status")),
            "source_institution": institution,
            "source_category": _clean(listed.get("category")),
            "source_period": _clean(listed.get("period")),
            "source_apply_period": _clean(listed.get("apply_period")),
            "source_target": _clean(listed.get("target")),
            "source_fee": _clean(listed.get("fee")),
            "source_material_fee": _clean(listed.get("material_fee")),
            "source_capacity": _clean(listed.get("capacity")),
            "detail_schedule": schedule,
            "detail_venue": venue,
            "online_payment": online_payment,
            "detail_verified": True,
            "visible_application_control_present": control,
            "application_control_contract": (
                "detail_courseSeq_bound_login_control" if control else "verified_no_control"
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
        "source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "raw_url_duplicate_count": 0,
        "semantic_duplicate_group_count": 0,
        "semantic_duplicate_excess_rows": 0,
        "historical_semantic_duplicate_group_count": 0,
        "historical_reversed_education_period_count": 0,
        "historical_reversed_application_period_count": 0,
        "current_reversed_period_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": error,
        "municipality_code": INJE_MUNICIPALITY_CODE,
        "municipality_name": INJE_MUNICIPALITY_NAME,
        "canonical_url": INJE_CANONICAL_URL,
        "ownership_scope": INJE_OWNERSHIP_SCOPE,
        "candidate_audit": {
            key: dict(value) for key, value in INJE_CANDIDATE_AUDIT.items()
        },
        "discovery_audit": dict(INJE_DISCOVERY_AUDIT),
        "duplicate_provider_aliases": [INJE_DUPLICATE_EDU_PROVIDER],
        "municipality_coverage": [INJE_MUNICIPALITY_CODE],
        "pii_fields_discarded": list(INJE_PII_FIELDS_DISCARDED),
        "pii_payload_persisted": False,
    }


def collect_inje_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 100,
    today: Optional[date | datetime | str] = None,
    max_workers: int = INJE_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete current/future two-scope education snapshot."""

    meta = _base_meta()
    if not is_inje_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match canonical Inje lifelong-education owner"
        )
        return [], INJE_PARSER, meta
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
        return [], INJE_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], INJE_PARSER, meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    workers = min(max_workers, INJE_MAX_WORKERS)
    meta["network_concurrency"] = workers

    def fetch_list(scope: str, page: int, *, sentinel: bool = False) -> _ListPage:
        soup = _fetch_soup(
            inje_list_url(scope, page),
            timeout=timeout,
            fetcher=current_fetcher,
            session_factory=current_factory,
        )
        return _parse_list_page(soup, scope, page, sentinel=sentinel)

    first_pages: dict[str, _ListPage] = {}
    first_errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(2, workers)) as pool:
        futures = {pool.submit(fetch_list, scope, 1): scope for scope in INJE_SCOPE_PATHS}
        for future in as_completed(futures):
            scope = futures[future]
            try:
                first_pages[scope] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                first_errors.append(
                    f"{scope} page 1: {type(exc).__name__}: {_clean(exc)}"
                )
    if first_errors or set(first_pages) != set(INJE_SCOPE_PATHS):
        meta["configured_collection_error"] = "; ".join(first_errors or ["first pages missing"])
        return [], INJE_PARSER, meta

    required = sum(first.data_last + 3 for first in first_pages.values())
    meta.update(
        {
            "required_list_requests": required,
            "sentinel_mode": "explicit_empty_with_displayed_page_reset_to_one",
            "declared_source_rows_by_scope": {
                scope: first_pages[scope].total for scope in INJE_SCOPE_PATHS
            },
            "declared_data_pages_by_scope": {
                scope: first_pages[scope].data_last for scope in INJE_SCOPE_PATHS
            },
            "declared_source_rows": sum(first.total for first in first_pages.values()),
            "declared_data_pages": sum(first.data_last for first in first_pages.values()),
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
        return [], INJE_PARSER, meta

    jobs: list[tuple[str, str, int, bool]] = []
    for scope, first in first_pages.items():
        jobs.extend((scope, "data", page, False) for page in range(2, first.data_last + 1))
        jobs.extend(
            (
                (scope, "sentinel", first.data_last + 1, True),
                (scope, "first_recheck", 1, False),
                (scope, "last_recheck", first.data_last, False),
            )
        )
    parsed_jobs: dict[tuple[str, str, int], _ListPage] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_list, scope, page, sentinel=sentinel): (
                scope,
                kind,
                page,
            )
            for scope, kind, page, sentinel in jobs
        }
        for future in as_completed(futures):
            scope, kind, page = futures[future]
            try:
                parsed_jobs[(scope, kind, page)] = future.result()
                meta["list_requests"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"{scope} {kind} page {page}: {type(exc).__name__}: {_clean(exc)}"
                )

    page_rows: dict[tuple[str, int], tuple[dict[str, Any], ...]] = {}
    per_scope_rows: dict[str, int] = {}
    per_scope_pages: dict[str, int] = {}
    sentinel_count = 0
    recheck_count = 0
    for scope, first in first_pages.items():
        for page in range(1, first.data_last + 1):
            parsed = first if page == 1 else parsed_jobs.get((scope, "data", page))
            if parsed is None:
                errors.append(f"{scope} data page {page}: response missing")
                continue
            if parsed.total != first.total or parsed.data_last != first.data_last:
                errors.append(f"{scope} data page {page}: total/page boundary changed")
            expected = min(
                INJE_PAGE_SIZE,
                max(0, first.total - (page - 1) * INJE_PAGE_SIZE),
            )
            if len(parsed.rows) != expected or parsed.empty_marker != (expected == 0):
                errors.append(
                    f"{scope} data page {page}: row count {len(parsed.rows)} != {expected}"
                )
            page_rows[(scope, page)] = parsed.rows
        sentinel = parsed_jobs.get((scope, "sentinel", first.data_last + 1))
        if sentinel is None:
            errors.append(f"{scope}: immediate post-last sentinel response missing")
        elif (
            sentinel.total != first.total
            or sentinel.data_last != first.data_last
            or sentinel.displayed_page != 1
            or sentinel.rows
            or not sentinel.empty_marker
        ):
            errors.append(f"{scope}: immediate post-last sentinel is not stable empty/reset")
        else:
            sentinel_count += 1
        first_recheck = parsed_jobs.get((scope, "first_recheck", 1))
        last_recheck = parsed_jobs.get((scope, "last_recheck", first.data_last))
        if first_recheck is None or last_recheck is None:
            errors.append(f"{scope}: first/last stability recheck response missing")
        else:
            recheck_count += 2
            if (
                first_recheck.total != first.total
                or first_recheck.data_last != first.data_last
                or _page_signature(first_recheck.rows) != _page_signature(first.rows)
            ):
                errors.append(f"{scope}: first-page stability recheck changed")
            if (
                last_recheck.total != first.total
                or last_recheck.data_last != first.data_last
                or _page_signature(last_recheck.rows)
                != _page_signature(page_rows.get((scope, first.data_last), ()))
            ):
                errors.append(f"{scope}: last-page stability recheck changed")
        scope_rows = sum(
            len(page_rows.get((scope, page), ()))
            for page in range(1, first.data_last + 1)
        )
        per_scope_rows[scope] = scope_rows
        per_scope_pages[scope] = sum(
            (scope, page) in page_rows for page in range(1, first.data_last + 1)
        )
        if scope_rows != first.total:
            errors.append(f"{scope}: complete row count {scope_rows} != {first.total}")

    listed = [
        row
        for scope in INJE_SCOPE_PATHS
        for page in range(1, first_pages[scope].data_last + 1)
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
    if len(listed) != sum(first.total for first in first_pages.values()):
        errors.append("two-scope complete row count changed")
    current_listed = [
        row
        for row in listed
        if (
            row["end"] >= cutoff
            or (
                row.get("education_period_reversed")
                and max(row["start"], row["end"]) >= cutoff
            )
        )
    ]
    current_reversed = [
        row
        for row in current_listed
        if row.get("education_period_reversed")
        or row.get("application_period_reversed")
    ]
    if current_reversed:
        errors.append(
            f"{len(current_reversed)} current/future rows have reversed source periods"
        )
    current_identities = {row["identity"] for row in current_listed}
    historical_rows = [
        row for row in listed if row["identity"] not in current_identities
    ]
    current_semantic = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("institution")),
        )
        for row in current_listed
    )
    semantic_groups = sum(value > 1 for value in current_semantic.values())
    semantic_excess = sum(max(0, value - 1) for value in current_semantic.values())
    all_semantic = Counter(
        (
            _normalized(row.get("title")),
            _clean(row.get("period")),
            _normalized(row.get("institution")),
        )
        for row in listed
    )
    historical_semantic_groups = sum(value > 1 for value in all_semantic.values())
    if semantic_groups:
        errors.append(f"{semantic_groups} current semantic duplicate groups")
    list_complete = bool(
        not errors
        and meta["list_requests"] == required
        and sentinel_count == len(INJE_SCOPE_PATHS)
        and recheck_count == 2 * len(INJE_SCOPE_PATHS)
        and len(listed) == sum(first.total for first in first_pages.values())
    )
    meta.update(
        {
            "data_pages": sum(per_scope_pages.values()),
            "data_pages_by_scope": per_scope_pages,
            "source_rows": len(listed),
            "source_rows_by_scope": per_scope_rows,
            "current_source_count": len(current_listed),
            "expired_count": len(listed) - len(current_listed),
            "sentinel_requests": sentinel_count,
            "stability_rechecks": recheck_count,
            "identity_duplicate_count": identity_duplicates,
            "raw_url_duplicate_count": raw_url_duplicates,
            "semantic_duplicate_group_count": semantic_groups,
            "semantic_duplicate_excess_rows": semantic_excess,
            "historical_semantic_duplicate_group_count": historical_semantic_groups,
            "historical_reversed_education_period_count": sum(
                bool(row.get("education_period_reversed")) for row in historical_rows
            ),
            "historical_reversed_application_period_count": sum(
                bool(row.get("application_period_reversed")) for row in historical_rows
            ),
            "current_reversed_period_count": len(current_reversed),
            "all_source_status_counts": dict(
                Counter(_clean(row.get("source_status")) for row in listed)
            ),
            "current_source_status_counts": dict(
                Counter(_clean(row.get("source_status")) for row in current_listed)
            ),
            "current_scope_counts": dict(
                Counter(_clean(row.get("source_scope")) for row in current_listed)
            ),
            "pagination_complete": list_complete,
        }
    )
    if not list_complete:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], INJE_PARSER, meta
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
        return [], INJE_PARSER, meta

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
                    raise InjeContractError("duplicate parsed detail identity")
                detailed[parsed_identity] = parsed
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                detail_errors.append(
                    f"detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
    errors.extend(detail_errors)
    meta["detail_errors"] = len(detail_errors)
    details_complete = bool(
        not detail_errors
        and meta["detail_pages"] == len(current_listed)
        and len(detailed) == len(current_listed)
    )
    ordered = [detailed[identity] for identity in identities if identity in detailed]
    controls_complete = bool(
        details_complete
        and all(
            (row.get("status") == "OPEN")
            == bool(row.get("raw_fields", {}).get("visible_application_control_present"))
            for row in ordered
        )
    )
    result: list[dict[str, Any]] = []
    if details_complete and controls_complete and not errors:
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
        list_complete and details_complete and controls_complete and not errors
    )
    if not snapshot_complete:
        result = []
    meta.update(
        {
            "returned_count": len(result),
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
            "institution_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_institution"))
                    for row in result
                )
            ),
            "scope_counts": dict(
                Counter(
                    _clean(row.get("raw_fields", {}).get("source_scope"))
                    for row in result
                )
            ),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
            "visible_public_application_control_count": sum(
                bool(
                    row.get("raw_fields", {}).get(
                        "visible_application_control_present"
                    )
                )
                for row in ordered
            ),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_listed),
            "no_current_reason": (
                "the complete official two-scope catalogue has no current/future courses"
                if snapshot_complete and not current_listed
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, INJE_PARSER, meta


collect = collect_inje_education


__all__ = [
    "INJE_CANONICAL_URL",
    "INJE_CANDIDATE_AUDIT",
    "INJE_DISCOVERY_AUDIT",
    "INJE_DUPLICATE_EDU_PROVIDER",
    "INJE_EDUCATION_SUPPORT_CANDIDATE_ID",
    "INJE_INSTITUTION_URL",
    "INJE_LANDING_CANDIDATE_ID",
    "INJE_MUNICIPALITY_CODE",
    "INJE_MUNICIPALITY_NAME",
    "INJE_PARSER",
    "INJE_PROVIDER",
    "configure_inje_verified_session",
    "collect",
    "collect_inje_education",
    "inje_detail_url",
    "inje_list_url",
    "is_inje_candidate_alias",
    "is_inje_education_target",
    "is_target",
]
