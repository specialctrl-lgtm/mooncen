"""Complete, fail-closed collectors for Ulleung-gun education owners.

Two independent owners are routed here:

* the Ulleung County lifelong-course ledger; and
* the Ulleung-gun Family Center programme ledger.

The county has two incumbent targets that render byte-identical content.
``mnu_uid=1845`` is only the parent/duplicate alias: the active menu, every
pagination link, every detail link, and every list return route use 1846.
Consequently provider ``...765C23CB`` (1846) is canonical and provider
``...283223C7`` (1845) is rejected rather than retargeted.  The Family Center
remains a separate owner and keeps its existing provider.

Each released snapshot traverses its complete audited scope, verifies an
immediate empty post-last sentinel, and rechecks the first, final, and sentinel
boundaries.  Details are requested only for current/future identities.  Login,
application, applicant, modal, download, and FamilyNet AJAX detail endpoints
are never requested.  Contacts, instructors, attachments, descriptions,
current applicant/wait-list counts, and other free-text payloads are omitted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


ULLEUNG_MUNICIPALITY_CODE = "4794000000"
ULLEUNG_MUNICIPALITY_NAME = "경상북도 울릉군"

ULLEUNG_FAMILY_HOST = "ulleunggun.familynet.or.kr"
ULLEUNG_FAMILY_PROVIDER = "MUNI_ULLEUNGGUN_FAMILYNET_OR_KR_10E2058E"
ULLEUNG_FAMILY_LIST_PATH = (
    "/center/lay1/program/S295T322C451/recruitReceipt/list.do"
)
ULLEUNG_FAMILY_DETAIL_PATH = (
    "/center/lay1/program/S295T322C451/recruitReceipt/view.do"
)
ULLEUNG_FAMILY_URL = (
    f"https://{ULLEUNG_FAMILY_HOST}{ULLEUNG_FAMILY_LIST_PATH}"
)
ULLEUNG_FAMILY_URL_SHA1 = "10E2058E5546D90433D2C63025EFC42B8F380811"
ULLEUNG_FAMILY_URL_SHA256 = (
    "550DC861404D5DFC559F2E02C7DC855442EE76BC61C7817866F73B1BF9C6F98A"
)
ULLEUNG_FAMILY_CANDIDATE_ID = "MUNI_IR_550DC861404D"

ULLEUNG_LIFELONG_HOST = "www.ulleung.go.kr"
ULLEUNG_LIFELONG_PROVIDER = "MUNI_WWW_ULLEUNG_GO_KR_765C23CB"
ULLEUNG_LIFELONG_PATH = "/edu/page.do"
ULLEUNG_LIFELONG_URL = (
    f"https://{ULLEUNG_LIFELONG_HOST}{ULLEUNG_LIFELONG_PATH}?mnu_uid=1846"
)
ULLEUNG_LIFELONG_URL_SHA1 = (
    "765C23CB5625E2A2879A69589307379B8C8D526C"
)
ULLEUNG_LIFELONG_URL_SHA256 = (
    "C0FCD9987AFADC19AAC9FC4744EF80D6C4CB426A47A3A9251B7AD8AB05A96973"
)
ULLEUNG_LIFELONG_CANDIDATE_ID = "MUNI_IR_C0FCD9987AFA"

ULLEUNG_LIFELONG_ALIAS_PROVIDER = "MUNI_WWW_ULLEUNG_GO_KR_283223C7"
ULLEUNG_LIFELONG_ALIAS_URL = (
    "https://www.ulleung.go.kr/edu/page.do?mnu_uid=1845&"
)
ULLEUNG_LIFELONG_ALIAS_NORMALIZED_URL = (
    "https://www.ulleung.go.kr/edu/page.do?mnu_uid=1845"
)
ULLEUNG_LIFELONG_ALIAS_URL_SHA1 = (
    "283223C735D41B4E870A92329F9561FF3AEA8712"
)
ULLEUNG_LIFELONG_ALIAS_URL_SHA256 = (
    "53F3519FD3BC1397F75C84068BB17F546FA83DB527E58E6720B206E77CF33DC6"
)
ULLEUNG_LIFELONG_ALIAS_NORMALIZED_SHA1 = (
    "FEA3843DC8E644E0D391B5F01156DF798C05240A"
)
ULLEUNG_LIFELONG_ALIAS_NORMALIZED_SHA256 = (
    "02223298A97E24901C4D0AEAA27F6E00AC1B1309984ABC57881327915FEE763D"
)
ULLEUNG_LIFELONG_ALIAS_CANDIDATE_ID = "MUNI_IR_02223298A97E"
ULLEUNG_LIFELONG_ALIAS_DECISION = (
    "deactivate MUNI_WWW_ULLEUNG_GO_KR_283223C7 as a duplicate parent-menu "
    "alias; pages 1, 2, 12 and the empty post-last page were byte-identical "
    "to 1846 on 2026-07-23, while the active menu, pagination, detail and "
    "return routes all canonically bind mnu_uid=1846; retain "
    "MUNI_WWW_ULLEUNG_GO_KR_765C23CB"
)

ULLEUNG_CANDIDATE_DECISIONS: Mapping[str, str] = {
    ULLEUNG_FAMILY_CANDIDATE_ID: (
        "retain_existing_separate_family_center_program_owner"
    ),
    ULLEUNG_LIFELONG_ALIAS_CANDIDATE_ID: (
        "deactivate_duplicate_parent_menu_alias_and_preserve_1846_incumbent"
    ),
    ULLEUNG_LIFELONG_CANDIDATE_ID: (
        "retain_1846_incumbent_as_canonical_complete_lifelong_owner"
    ),
}

ULLEUNG_OWNER_BOUNDARIES: Mapping[str, str] = {
    ULLEUNG_FAMILY_URL: "separate_family_center_application_identity_owner",
    ULLEUNG_LIFELONG_URL: "canonical_county_lifelong_course_identity_owner",
    ULLEUNG_LIFELONG_ALIAS_URL: "duplicate_parent_menu_alias_deactivate",
    "https://www.ulleung.go.kr/edu/page.do?mnu_uid=1868": (
        "same_county_derived_education_calendar_not_separate_identities"
    ),
    "https://www.ulleung.go.kr/edu/page.do?mnu_uid=1848": (
        "family_center_navigation_bridge_not_county_course_owner"
    ),
    "https://www.familydb.or.kr/": (
        "upstream_family_platform_detail_backend_not_a_municipal_owner"
    ),
    "https://www.gbelib.kr/ul/index.do": (
        "separate_provincial_education_office_library_owner"
    ),
    "https://www.dokdomuseum.go.kr/": "separate_museum_experience_owner",
}

ULLEUNG_LIFELONG_BRANCHES: Mapping[str, str] = {
    "문화체육과": "문화체육과",
    "관광문화체육실": "관광문화체육실",
    "미래전략추진단": "미래전략추진단",
    "농업기술센터": "농업기술센터",
}
ULLEUNG_LIFELONG_CATEGORIES = (
    "기초문해교육",
    "학력보완교육",
    "인문교양교육",
    "문화예술교육",
    "직업능력교육",
    "시민참여교육",
)
ULLEUNG_LIFELONG_CATEGORY_CODES: Mapping[str, str] = {
    "159": "기초문해교육",
    "160": "학력보완교육",
    "161": "인문교양교육",
    "184": "문화예술교육",
    "185": "직업능력교육",
    "186": "시민참여교육",
}
ULLEUNG_LIFELONG_TARGETS: Mapping[str, str] = {
    "166": "유아",
    "167": "초등학생",
    "180": "중학생",
    "181": "고등학생",
    "187": "성인",
    "188": "어르신(만65세 이상)",
    "189": "누구나",
}

ULLEUNG_LIFELONG_EVENT_CORRECTIONS: Mapping[str, tuple[str, str]] = {
    "1012": (
        "프랑스자수(울릉도의 자연을 수 놓다) 2차",
        "2024-11-18 ~ 20241118",
    )
}
ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS: Mapping[str, tuple[str, str]] = {
    "28": (
        "향기치유 - 아로마테라피",
        "2024-09-23 00시 ~ 20241101 23시 (접수인원 : 12/12)",
    ),
    "27": (
        "향기치유 - 아로마테라피",
        "2024-09-23 00시 ~ 20241101 23시 (접수인원 : 12/12)",
    ),
}
ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT: Mapping[str, tuple[str, str]] = {
    "2005": (
        "K-pop 방송댄스(오후 프로그램과 통합되었습니다.)",
        "2024-12-28 00시 ~ 2025-01-01 18시",
    )
}

ULLEUNG_PAGE_SIZE = 10
ULLEUNG_FAMILY_PAGE_SIZE = 5
ULLEUNG_FETCH_ATTEMPTS = 2
ULLEUNG_MAX_WORKERS = 4
ULLEUNG_LIFELONG_PARSER = (
    "ulleung_lifelong_complete_1846_owner+1845_duplicate_alias_rejection+"
    "complete_identity_pagination+empty_post_last_sentinel+stable_boundaries+"
    "current_future_detail_binding+application_endpoint_no_fetch+pii_allowlist"
)
ULLEUNG_FAMILY_PARSER = (
    "ulleung_family_center_current_future_all_status+default_open_reconciliation+"
    "complete_pagination+empty_post_last_sentinel+stable_boundaries+"
    "detail_shell_no_ajax_or_application_fetch+pii_allowlist"
)


class UlleungContractError(RuntimeError):
    """Raised when an audited Ulleung public-source contract changes."""


@dataclass(frozen=True)
class _FamilyProgram:
    identity: str
    title: str
    source_status: str
    event_start: date
    event_end: date
    raw_event_period: str
    apply_start: date
    apply_end: date
    raw_apply_period: str
    rounds: int
    venue: str
    page: int
    detail_url: str


@dataclass(frozen=True)
class _LifelongCourse:
    identity: str
    title: str
    category: str
    branch: str
    source_status: str
    event_start: date
    event_end: date
    raw_event_period: str
    apply_start: date
    apply_end: date
    raw_apply_period: str
    additional_apply_period: str
    schedule: str
    target: str
    capacity_total: int
    page: int
    detail_url: str


@dataclass(frozen=True)
class _LifelongDetail:
    venue: str
    schedule: str
    target: str
    capacity_total: int
    fee: str
    material_fee: str
    control: str


Fetcher = Callable[..., Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})$"
)
_FAMILY_DATETIME_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s+([01]\d|2[0-3]):([0-5]\d)\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s+([01]\d|2[0-3]):([0-5]\d)$"
)
_FAMILY_TOTAL_RE = re.compile(
    r"^전체\s*:\s*([\d,]+)\s*\(\s*(\d+)\s*/\s*(\d+)\s*페이지\)$"
)
_FAMILY_ROUNDS_RE = re.compile(r"^총\s*(\d+)회$")
_LIFELONG_RECEIPT_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s+(\d{2})시\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s+(\d{2})시\s*"
    r"\(접수인원\s*:\s*(\d+)\s*/\s*(\d+)\)$"
)
_LIFELONG_ADDITIONAL_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s+(\d{2})시\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s+(\d{2})시$"
)
_LIFELONG_DETAIL_RECEIPT_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s+(\d{2})시\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s+(\d{2})시$"
)
_LIFELONG_DETAIL_EVENT_RE = re.compile(
    r"^(20\d{2})-(\d{2})-(\d{2})\s*~\s*"
    r"(20\d{2})-(\d{2})-(\d{2})\s*"
    r"\(([^()]*)\)$"
)
_LIFELONG_DETAIL_CAPACITY_RE = re.compile(
    r"^신청정원\s*:\s*(\d+)\s*\(온라인\s*:\s*\d+\s*/\s*"
    r"오프라인\s*:\s*\d+\s*\)\s*/\s*후보정원\s*:\s*\d+$"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_FAMILY_STATUS_CLASSES: Mapping[str, str] = {
    "접수중": "c0",
    "접수예정": "c1",
    "접수마감": "c2",
    "진행중": "c3",
    "완료": "c4",
}
_LIFELONG_STATUS_CLASSES: Mapping[str, str] = {
    "접수예정": "st01",
    "접수대기": "st01",
    "접수중": "st02",
    "접수마감": "st03",
}
_LIFELONG_DETAIL_FIELDS = (
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
    "모집방법",
    "모집안내",
    "주의사항",
    "첨부파일",
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _target_extra(target: Any) -> Mapping[str, Any]:
    value = _target_value(target, "extra")
    return value if isinstance(value, Mapping) else {}


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _safe_https_url(url: str, *, host: str, path: str) -> bool:
    parsed = urlparse(url)
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == host
        and parsed.port is None
        and parsed.path == path
        and not parsed.params
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query, keep_blank_values=True)


def is_ulleung_family_target(target: Any) -> bool:
    url = _target_url(target)
    return bool(
        _provider(target) == ULLEUNG_FAMILY_PROVIDER
        and _safe_https_url(
            url, host=ULLEUNG_FAMILY_HOST, path=ULLEUNG_FAMILY_LIST_PATH
        )
        and not urlparse(url).query
    )


def is_ulleung_lifelong_target(target: Any) -> bool:
    url = _target_url(target)
    return bool(
        _provider(target) == ULLEUNG_LIFELONG_PROVIDER
        and _safe_https_url(
            url, host=ULLEUNG_LIFELONG_HOST, path=ULLEUNG_LIFELONG_PATH
        )
        and _query(url) == {"mnu_uid": ["1846"]}
    )


def is_ulleung_education_target(target: Any) -> bool:
    return is_ulleung_family_target(target) or is_ulleung_lifelong_target(target)


is_target = is_ulleung_education_target


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


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _same_request_url(actual: str, requested: str) -> bool:
    left, right = urlparse(actual), urlparse(requested)
    return bool(
        left.scheme.lower() == "https"
        and (left.hostname or "").rstrip(".").lower()
        == (right.hostname or "").rstrip(".").lower()
        and left.port is None
        and left.path == right.path
        and parse_qs(left.query, keep_blank_values=True)
        == parse_qs(right.query, keep_blank_values=True)
        and not left.fragment
    )


def _response_soup(response: Any, requested: str) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise UlleungContractError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise UlleungContractError("HTTP redirects are not accepted")
    final_url = _clean(getattr(response, "url", ""))
    if not _same_request_url(final_url, requested):
        raise UlleungContractError("source response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise UlleungContractError("empty HTML response")
    return BeautifulSoup(content, "lxml"), final_url


def _request_soup(
    current: Any,
    url: str,
    *,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[BeautifulSoup, str, int]:
    messages: list[str] = []
    for attempt in range(1, ULLEUNG_FETCH_ATTEMPTS + 1):
        try:
            if fetcher is not None:
                result = fetcher(current, "GET", url, timeout=timeout, data={})
                if (
                    isinstance(result, tuple)
                    and len(result) == 2
                    and isinstance(result[0], BeautifulSoup)
                ):
                    soup, final_url = result
                    final_url = _clean(final_url or url)
                    if not _same_request_url(final_url, url):
                        raise UlleungContractError("source response URL changed")
                    return soup, final_url, attempt
                if isinstance(result, BeautifulSoup):
                    return result, url, attempt
                if isinstance(result, (str, bytes, bytearray)):
                    if not result:
                        raise UlleungContractError("empty HTML response")
                    return BeautifulSoup(result, "lxml"), url, attempt
                soup, final_url = _response_soup(result, url)
                return soup, final_url, attempt
            soup, final_url = _response_soup(current.get(url, timeout=timeout), url)
            return soup, final_url, attempt
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
    raise UlleungContractError("; ".join(messages))


def _digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _date_value(year: str, month: str, day: str, identity: str) -> date:
    try:
        return date(int(year), int(month), int(day))
    except ValueError as exc:
        raise UlleungContractError(f"identity {identity}: invalid source date") from exc


def _date_range(value: Any, identity: str) -> tuple[date, date]:
    text = _clean(value)
    match = _DATE_RANGE_RE.fullmatch(text)
    if match is None:
        raise UlleungContractError(f"identity {identity}: malformed date range {text!r}")
    values = match.groups()
    start = _date_value(*values[:3], identity)
    end = _date_value(*values[3:], identity)
    if start > end:
        raise UlleungContractError(f"identity {identity}: reversed date range")
    return start, end


def _family_datetime_range(value: Any, identity: str) -> tuple[date, date]:
    text = _clean(value)
    match = _FAMILY_DATETIME_RE.fullmatch(text)
    if match is None:
        raise UlleungContractError(
            f"family programme {identity}: malformed receipt period"
        )
    values = match.groups()
    start = _date_value(*values[:3], identity)
    end = _date_value(*values[5:8], identity)
    if start > end:
        raise UlleungContractError(
            f"family programme {identity}: reversed receipt period"
        )
    return start, end


def _branch_code(provider: str, branch: str) -> str:
    suffix = hashlib.sha1(branch.encode("utf-8")).hexdigest().upper()[:8]
    return f"{provider}:education:{suffix}"


def _base_output(target: Any) -> dict[str, Any]:
    extra = _target_extra(target)
    return {
        "collection_category": _clean(extra.get("collection_category") or "공공예약"),
        "domain_category": _clean(extra.get("domain_category") or "교육·강좌"),
        "operator_type": _clean(extra.get("operator_type") or "지자체/공공기관"),
        "source_group": _clean(extra.get("source_group") or "municipal_reservation"),
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "program_type": "교육",
        "municipality_code": ULLEUNG_MUNICIPALITY_CODE,
        "municipality_name": ULLEUNG_MUNICIPALITY_NAME,
        "municipality_full_name": ULLEUNG_MUNICIPALITY_NAME,
    }


def _privacy_valid(rows: Iterable[Mapping[str, Any]]) -> bool:
    forbidden_keys = {
        "instructor",
        "teacher",
        "contact",
        "phone",
        "email",
        "attachment",
        "content",
        "body",
        "capacity_current",
        "waitlist_current",
        "applicants",
        "manager",
    }
    material = repr(list(rows))
    if _PHONE_RE.search(material) or _EMAIL_RE.search(material):
        return False
    for row in rows:
        raw_fields = row.get("raw_fields", {})
        if not isinstance(raw_fields, Mapping):
            return False
        if forbidden_keys.intersection(str(key).lower() for key in raw_fields):
            return False
    return True


def ulleung_family_detail_url(identity: str, title: str) -> str:
    if not _IDENTITY_RE.fullmatch(_clean(identity)) or not _clean(title):
        return ""
    return (
        f"https://{ULLEUNG_FAMILY_HOST}{ULLEUNG_FAMILY_DETAIL_PATH}?"
        + urlencode((("seq", _clean(identity)), ("progNm", _clean(title))))
    )


def _family_query(
    audit_date: date, page: int, *, scope: str
) -> tuple[tuple[str, str], ...]:
    if page < 1 or scope not in {"active", "current_all"}:
        return ()
    if scope == "active":
        return () if page == 1 else (("rows", "5"), ("cpage", str(page)))
    return (
        ("rows", "5"),
        ("cpage", str(page)),
        ("status", "all_program_status"),
        ("program_start_date", audit_date.isoformat()),
        ("program_end_date", "2099-12-31"),
        ("reception_start_date", "2000-01-01"),
        ("reception_end_date", "2099-01-01"),
        ("area", "A009"),
        ("area_detail", "D116"),
    )


def ulleung_family_list_url(
    audit_date: date, page: int = 1, *, scope: str = "current_all"
) -> str:
    query = _family_query(audit_date, page, scope=scope)
    return ULLEUNG_FAMILY_URL if not query else f"{ULLEUNG_FAMILY_URL}?{urlencode(query)}"


def _family_form_value(form: Any, selector: str) -> str:
    nodes = form.select(selector)
    if len(nodes) != 1:
        return ""
    return _clean(nodes[0].get("value"))


def _validate_family_form(
    soup: BeautifulSoup, *, scope: str, page: int, audit_date: date
) -> None:
    forms = soup.select(".program_search form#searchForm[name='searchForm']")
    if len(forms) != 1:
        raise UlleungContractError("family search form changed")
    form = forms[0]
    action = urlparse(urljoin(ULLEUNG_FAMILY_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).lower() != "get"
        or action.hostname != ULLEUNG_FAMILY_HOST
        or action.path != ULLEUNG_FAMILY_LIST_PATH
        or action.query
        or _clean(form.get("onsubmit")) != "return setForm();"
    ):
        raise UlleungContractError("family search form transport changed")
    expected_page = str(page)
    if (
        _family_form_value(form, "input[name='rows']") != "5"
        or _family_form_value(form, "input[name='cpage']") != expected_page
        or _family_form_value(form, "input#area[name='area']") != "A009"
        or _family_form_value(form, "input#area_detail[name='area_detail']")
        != "D116"
        or _family_form_value(form, "input[name='cat']")
        or _family_form_value(form, "input[name='keyword']")
    ):
        raise UlleungContractError("family unfiltered owner/page form changed")
    center_blocks = form.select(":scope > ul > li:first-child .right")
    if (
        len(center_blocks) != 1
        or _clean(center_blocks[0].get_text(" ", strip=True))
        != "가족센터 > 경북 > 울릉군"
    ):
        raise UlleungContractError("family center branch binding changed")
    application_types = [
        (
            _clean(node.get("id")),
            node.has_attr("checked"),
            _clean(form.select_one(f"label[for='{_clean(node.get('id'))}']").get_text(" ", strip=True))
            if form.select_one(f"label[for='{_clean(node.get('id'))}']")
            else "",
        )
        for node in form.select("input[name='application_type']")
    ]
    if application_types != [
        ("family_program", True, "가족센터프로그램"),
        ("family_consultion", False, "가족상담"),
        ("multicultural_family_service", False, "다문화가족서비스"),
        ("family_hope_dream", False, "온가족보듬"),
    ]:
        raise UlleungContractError("family application-type vocabulary changed")
    status_nodes = form.select(".program_status input")
    statuses = [
        (_clean(node.get("id")), node.has_attr("checked")) for node in status_nodes
    ]
    expected_statuses = [
        ("all_program_status", scope == "current_all"),
        ("plan", False),
        ("ongoing", scope == "active"),
        ("finish", False),
    ]
    if statuses != expected_statuses:
        raise UlleungContractError("family status filter changed")
    selected_program = form.select("#program_date_select option[selected]")
    selected_receipt = form.select("#reception_date_select option[selected]")
    if (
        len(selected_program) != 1
        or _clean(selected_program[0].get("value")) != "program_term"
        or len(selected_receipt) != 1
        or _clean(selected_receipt[0].get("value")) != "reception_term"
    ):
        raise UlleungContractError("family date-filter mode changed")
    if scope == "current_all":
        expected_dates = {
            "#program_start_date_term": audit_date.isoformat(),
            "#program_end_date_term": "2099-12-31",
            "#reception_start_date_term": "2000-01-01",
            "#reception_end_date_term": "2099-01-01",
        }
        if any(
            _family_form_value(form, selector) != expected
            for selector, expected in expected_dates.items()
        ):
            raise UlleungContractError("family current/future date scope changed")


def _family_total(soup: BeautifulSoup, page: int) -> tuple[int, int]:
    nodes = soup.select(".list_option.apply_type1 > p.hit")
    if len(nodes) != 1:
        raise UlleungContractError("family declared total changed")
    match = _FAMILY_TOTAL_RE.fullmatch(_clean(nodes[0].get_text(" ", strip=True)))
    if match is None or int(match.group(2)) != page:
        raise UlleungContractError("family declared page changed")
    total, last_page = int(match.group(1).replace(",", "")), int(match.group(3))
    if last_page != max(1, math.ceil(total / ULLEUNG_FAMILY_PAGE_SIZE)):
        raise UlleungContractError("family declared total/final page mismatch")
    return total, last_page


def _validate_family_pager(
    soup: BeautifulSoup,
    *,
    scope: str,
    page: int,
    last_page: int,
    audit_date: date,
    sentinel: bool,
) -> None:
    pagers = soup.select(".paging #pagingWrap")
    if len(pagers) != 1:
        raise UlleungContractError("family pagination block changed")
    current = pagers[0].select(":scope .num > b > a")
    if sentinel:
        if current:
            raise UlleungContractError("family post-last page unexpectedly has focus")
    elif len(current) != 1 or _clean(current[0].get_text(" ", strip=True)) != str(page):
        raise UlleungContractError("family current page focus changed")
    for anchor in pagers[0].select("a[href]"):
        href = _clean(anchor.get("href"))
        if href == "javascript:void(0);":
            if anchor not in current:
                raise UlleungContractError("family unexpected JavaScript pager control")
            continue
        actual = urljoin(ULLEUNG_FAMILY_URL, href)
        query = _query(actual)
        values = query.get("cpage", [])
        if len(values) != 1 or not values[0].isdigit():
            raise UlleungContractError("family pager page is malformed")
        target_page = int(values[0])
        expected = (
            f"{ULLEUNG_FAMILY_URL}?"
            + urlencode((("rows", "5"), ("cpage", str(target_page))))
            if scope == "active"
            else ulleung_family_list_url(audit_date, target_page, scope=scope)
        )
        if not 1 <= target_page <= last_page or not _same_request_url(actual, expected):
            raise UlleungContractError("family pager route escaped audited scope")


def _family_card_fields(card: Any, identity: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for item in card.select(":scope > .txt > ul > li"):
        labels = item.select(":scope > p > b")
        if len(labels) != 1:
            raise UlleungContractError(
                f"family programme {identity}: malformed list field"
            )
        label = _clean(labels[0].get_text(" ", strip=True))
        if not label or label in output:
            raise UlleungContractError(
                f"family programme {identity}: duplicate list field"
            )
        text = _clean(item.select_one(":scope > p").get_text(" ", strip=True))
        if not text.startswith(label):
            raise UlleungContractError(
                f"family programme {identity}: list label/value changed"
            )
        value = _clean(text[len(label) :])
        if label == "진행장소" and value.endswith("오시는길"):
            value = _clean(value[: -len("오시는길")])
        output[label] = value
    if tuple(output) != ("회차정보", "행사기간", "접수기간", "진행장소"):
        raise UlleungContractError(
            f"family programme {identity}: list field vocabulary changed"
        )
    return output


def _family_send_identity(value: Any, identity: str, kind: str) -> str:
    text = _clean(value)
    match = re.fullmatch(
        r"send\('([1-9]\d*)','(?:\\'|[^'])*','(web|center)'\)", text
    )
    if match is None or match.group(1) != identity or match.group(2) != kind:
        raise UlleungContractError(
            f"family programme {identity}: {kind} identity control changed"
        )
    return identity


def _family_key(row: _FamilyProgram) -> tuple[Any, ...]:
    return (
        row.identity,
        row.title,
        row.source_status,
        row.raw_event_period,
        row.raw_apply_period,
        row.rounds,
        row.venue,
    )


def _parse_family_page(
    soup: BeautifulSoup,
    *,
    scope: str,
    page: int,
    audit_date: date,
    sentinel: bool = False,
) -> tuple[int, int, list[_FamilyProgram], str]:
    _validate_family_form(soup, scope=scope, page=page, audit_date=audit_date)
    total, last_page = _family_total(soup, page)
    _validate_family_pager(
        soup,
        scope=scope,
        page=page,
        last_page=last_page,
        audit_date=audit_date,
        sentinel=sentinel,
    )
    containers = soup.select(".program_list.apply_type1 > ul")
    if len(containers) != 1:
        raise UlleungContractError("family programme list changed")
    cards = containers[0].select(":scope > li.clearfix")
    expected = (
        0
        if sentinel
        else min(
            ULLEUNG_FAMILY_PAGE_SIZE,
            max(0, total - (page - 1) * ULLEUNG_FAMILY_PAGE_SIZE),
        )
    )
    if len(cards) != expected:
        raise UlleungContractError(
            f"family page {page}: expected {expected} cards, found {len(cards)}"
        )
    rows: list[_FamilyProgram] = []
    for card in cards:
        titles = card.select(":scope > .txt > p.tit > a")
        states = card.select(":scope > .util > .state")
        locations = card.select(":scope > .util > .loc")
        if len(titles) != 1 or len(states) != 1 or len(locations) != 1:
            raise UlleungContractError("family programme card structure changed")
        title_control = titles[0]
        identity_match = re.match(
            r"send\('([1-9]\d*)'", _clean(title_control.get("onclick"))
        )
        identity = identity_match.group(1) if identity_match else ""
        if not _IDENTITY_RE.fullmatch(identity):
            raise UlleungContractError("family programme identity is missing")
        _family_send_identity(title_control.get("onclick"), identity, "web")
        if _clean(title_control.get("href")) != "javascript:void(0);":
            raise UlleungContractError(
                f"family programme {identity}: title route changed"
            )
        title = _clean(title_control.get_text(" ", strip=True))
        if not title:
            raise UlleungContractError(f"family programme {identity}: empty title")
        status_nodes = states[0].select(":scope > span")
        detail_controls = states[0].select(":scope > a")
        if len(status_nodes) != 1 or len(detail_controls) != 1:
            raise UlleungContractError(
                f"family programme {identity}: state controls changed"
            )
        source_status = _clean(status_nodes[0].get_text(" ", strip=True))
        if (
            source_status not in _FAMILY_STATUS_CLASSES
            or set(status_nodes[0].get("class", []))
            != {_FAMILY_STATUS_CLASSES[source_status]}
        ):
            raise UlleungContractError(
                f"family programme {identity}: unknown source status"
            )
        control = detail_controls[0]
        if (
            _clean(control.get_text(" ", strip=True)) != "신청하기"
            or _clean(control.get("href")) != "javascript:void(0);"
        ):
            raise UlleungContractError(
                f"family programme {identity}: public detail control changed"
            )
        _family_send_identity(control.get("onclick"), identity, "center")
        if (
            _clean(locations[0].get_text(" ", strip=True)) != "경북 > 울릉군"
            or len(locations[0].select(":scope > b")) != 1
        ):
            raise UlleungContractError(
                f"family programme {identity}: owner branch changed"
            )
        fields = _family_card_fields(card, identity)
        rounds_match = _FAMILY_ROUNDS_RE.fullmatch(fields["회차정보"])
        if rounds_match is None:
            raise UlleungContractError(
                f"family programme {identity}: malformed round count"
            )
        event_start, event_end = _date_range(fields["행사기간"], identity)
        apply_start, apply_end = _family_datetime_range(
            fields["접수기간"], identity
        )
        if scope == "active" and source_status != "접수중":
            raise UlleungContractError("family default active view contains non-open row")
        if event_end < audit_date:
            raise UlleungContractError(
                f"family programme {identity}: current/future scope contains expired row"
            )
        rows.append(
            _FamilyProgram(
                identity=identity,
                title=title,
                source_status=source_status,
                event_start=event_start,
                event_end=event_end,
                raw_event_period=fields["행사기간"],
                apply_start=apply_start,
                apply_end=apply_end,
                raw_apply_period=fields["접수기간"],
                rounds=int(rounds_match.group(1)),
                venue=fields["진행장소"],
                page=page,
                detail_url=ulleung_family_detail_url(identity, title),
            )
        )
    return total, last_page, rows, _digest(tuple(_family_key(row) for row in rows))


def _parse_family_detail_shell(soup: BeautifulSoup, program: _FamilyProgram) -> None:
    roots = soup.select(".sub_contents > .program_view")
    if len(roots) != 1:
        raise UlleungContractError(
            f"family programme {program.identity}: detail shell changed"
        )
    root = roots[0]
    expected_hidden = {
        "input#seq[name='seq']": program.identity,
        "input[name='familynet_pg_no']": program.identity,
        "input#area[name='area']": "A009",
        "input#area_detail[name='area_detail']": "D116",
        "input#progNm": program.title,
    }
    for selector, expected in expected_hidden.items():
        nodes = root.select(selector)
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            raise UlleungContractError(
                f"family programme {program.identity}: detail identity binding changed"
            )
    table = root.select("table.view_style_1")
    if len(table) != 1:
        raise UlleungContractError(
            f"family programme {program.identity}: detail table shell changed"
        )
    field_ids = [
        _clean(node.get("id"))
        for node in table[0].select("tbody .txt span[id], tbody .txt strong[id]")
    ]
    expected_ids = [
        "center_nm",
        "program_date_time",
        "reception_date_time",
        "participation_target",
        "recruit_personal_cnt",
        "waiting_personal_cnt",
        "program_conts",
        "eposidoe_detail",
        "program_place",
    ]
    if field_ids != expected_ids:
        raise UlleungContractError(
            f"family programme {program.identity}: detail field shell changed"
        )
    buttons = root.select(":scope .btn_type1 .center > a")
    controls = [
        (
            _clean(node.get("id")),
            _clean(node.get_text(" ", strip=True)),
            _clean(node.get("href")),
            _clean(node.get("style")),
        )
        for node in buttons
    ]
    if controls != [
        (
            "applyBtn",
            "신청하기",
            "javascript:applysMethods.modal.openApply();",
            "display:none;",
        ),
        ("applyCompleteBtn", "신청완료", "#", "display:none;"),
        ("", "목록", ULLEUNG_FAMILY_LIST_PATH, ""),
    ]:
        raise UlleungContractError(
            f"family programme {program.identity}: detail controls changed"
        )
    script = "\n".join(node.get_text("\n") for node in soup.find_all("script"))
    for route in (
        "/recruitReceipt/getView.do",
        "/recruitReceipt/loginCheck.do",
        "/recruitReceipt/modal/apply.do",
    ):
        if route not in script:
            raise UlleungContractError(
                f"family programme {program.identity}: guarded route contract changed"
            )


def _family_effective_status(program: _FamilyProgram, audit_date: date) -> str:
    if program.source_status == "접수예정":
        return "SCHEDULED"
    if (
        program.source_status == "접수중"
        and program.apply_start <= audit_date <= program.apply_end
    ):
        return "OPEN"
    return "CLOSED"


def _family_output(
    target: Any, program: _FamilyProgram, audit_date: date
) -> dict[str, Any]:
    status = _family_effective_status(program, audit_date)
    application_url = program.detail_url if status == "OPEN" else ""
    output: dict[str, Any] = {
        "provider": ULLEUNG_FAMILY_PROVIDER,
        "provider_course_id": (
            f"{ULLEUNG_FAMILY_PROVIDER}:education:program:{program.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": program.title,
        "branch": "울릉군 가족센터",
        "branch_code": _branch_code(ULLEUNG_FAMILY_PROVIDER, "울릉군 가족센터"),
        "preserve_branch": True,
        "branch_url": ULLEUNG_FAMILY_URL,
        "raw_url": program.detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_APPLICATION" if application_url else "INFORMATION_ONLY",
        "application_method_raw": "가족센터 온라인 신청" if application_url else "정보 제공",
        "reservation_available": bool(application_url),
        "status": status,
        "period": f"{program.event_start.isoformat()} ~ {program.event_end.isoformat()}",
        "start_date": program.event_start.isoformat(),
        "end_date": program.event_end.isoformat(),
        "apply_period": f"{program.apply_start.isoformat()} ~ {program.apply_end.isoformat()}",
        "apply_start_date": program.apply_start.isoformat(),
        "apply_end_date": program.apply_end.isoformat(),
        "schedule_raw": f"총 {program.rounds}회",
        "target": "",
        "capacity": "",
        "capacity_total": None,
        "fee": "",
        "venue_name": program.venue,
        "room": program.venue,
        "category": "가족교육·지원",
        "collection_type": ULLEUNG_FAMILY_PARSER,
        "raw_fields": {
            "parser": ULLEUNG_FAMILY_PARSER,
            "source_kind": "familynet_current_future",
            "source_identity": program.identity,
            "source_page": program.page,
            "source_status": program.source_status,
            "source_event_period": program.raw_event_period,
            "source_apply_period": program.raw_apply_period,
            "rounds": program.rounds,
            "detail_shell_verified": True,
            "detail_ajax_fetched": False,
            "application_control_present": bool(application_url),
            "application_endpoint_fetched": False,
            "service_family": "education",
        },
    }
    output.update(_base_output(target))
    return output


def _family_contract_key(program: _FamilyProgram) -> tuple[Any, ...]:
    """Return the owner identity payload without the view-specific page number."""

    return (
        program.identity,
        program.title,
        program.source_status,
        program.event_start,
        program.event_end,
        program.raw_event_period,
        program.apply_start,
        program.apply_end,
        program.raw_apply_period,
        program.rounds,
        program.venue,
        program.detail_url,
    )


def ulleung_lifelong_list_url(page: int = 1) -> str:
    if page < 1:
        return ""
    if page == 1:
        return ULLEUNG_LIFELONG_URL
    return (
        f"https://{ULLEUNG_LIFELONG_HOST}{ULLEUNG_LIFELONG_PATH}?"
        + urlencode((("pageNo", str(page)), ("mnu_uid", "1846")))
    )


def ulleung_lifelong_detail_url(identity: str) -> str:
    if not _IDENTITY_RE.fullmatch(_clean(identity)):
        return ""
    return (
        f"https://{ULLEUNG_LIFELONG_HOST}{ULLEUNG_LIFELONG_PATH}?"
        + urlencode(
            (
                ("cmd", "2"),
                ("mnu_uid", "1846"),
                ("lctre_uid", _clean(identity)),
            )
        )
    )


def _checkbox_contract(
    root: Any,
    selector: str,
) -> list[tuple[str, str, str, bool]]:
    output: list[tuple[str, str, str, bool]] = []
    for node in root.select(selector):
        identity = _clean(node.get("id"))
        labels = root.select(f"label[for='{identity}']") if identity else []
        if len(labels) != 1:
            return []
        output.append(
            (
                _clean(node.get("name")),
                _clean(node.get("value")),
                _clean(labels[0].get_text(" ", strip=True)),
                node.has_attr("checked"),
            )
        )
    return output


def _validate_lifelong_form(soup: BeautifulSoup) -> None:
    forms = soup.select(".wrap_srch_lecture > form#frm[name='frm']")
    if len(forms) != 1:
        raise UlleungContractError("lifelong search form changed")
    form = forms[0]
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action"))
    ):
        raise UlleungContractError("lifelong search transport changed")
    scalar_values = {
        "input#srchKwd[name='srchKwd']": "",
        "input#srchStart[name='srchStart']": "",
        "input#srchEnd[name='srchEnd']": "",
        "input#d_search_ch[name='d_search_ch']": "",
        "input#e_search_arr[name='e_search_arr']": "",
        "input#f_search_arr[name='f_search_arr']": "",
    }
    for selector, expected in scalar_values.items():
        nodes = form.select(selector)
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            raise UlleungContractError("lifelong unfiltered form state changed")
    expected_targets = [
        ("srchTrgt", code, label, False)
        for code, label in ULLEUNG_LIFELONG_TARGETS.items()
    ]
    if _checkbox_contract(form, "input[name='srchTrgt']") != expected_targets:
        raise UlleungContractError("lifelong target vocabulary changed")
    expected_categories = [
        ("srchFld", code, label, False)
        for code, label in ULLEUNG_LIFELONG_CATEGORY_CODES.items()
    ]
    if _checkbox_contract(form, "input[name='srchFld']") != expected_categories:
        raise UlleungContractError("lifelong category vocabulary changed")
    expected_days = [
        (f"srchWeek_dy{index}", "Y", label, False)
        for index, label in enumerate(("월", "화", "수", "목", "금", "토", "일"))
    ]
    if _checkbox_contract(form, "input[name^='srchWeek_dy']") != expected_days:
        raise UlleungContractError("lifelong weekday vocabulary changed")
    if _checkbox_contract(form, "input[name='srchTmzon']") != [
        ("srchTmzon", "A", "오전", False),
        ("srchTmzon", "B", "오후", False),
        ("srchTmzon", "C", "야간", False),
    ]:
        raise UlleungContractError("lifelong time-zone vocabulary changed")
    if _checkbox_contract(form, "input[name='srchStts']") != [
        ("srchStts", "A", "접수예정", False),
        ("srchStts", "B", "접수중", False),
        ("srchStts", "C", "접수마감", False),
    ]:
        raise UlleungContractError("lifelong status vocabulary changed")
    fee_nodes = form.select("input[name^='srchFee_']")
    if _checkbox_contract(form, "input[name^='srchFee_']") != [
        ("srchFee_0", "isFree", "무료", False),
        ("srchFee_1", "isPay", "유료", False),
    ] or len(fee_nodes) != 2:
        raise UlleungContractError("lifelong fee vocabulary changed")


def _lifelong_href_page(anchor: Any) -> int:
    href = _clean(anchor.get("href"))
    if not href:
        return 0
    actual = urljoin(ULLEUNG_LIFELONG_URL, href)
    if not _safe_https_url(
        actual, host=ULLEUNG_LIFELONG_HOST, path=ULLEUNG_LIFELONG_PATH
    ):
        return 0
    query = _query(actual)
    if set(query) != {"pageNo", "mnu_uid"} or query.get("mnu_uid") != ["1846"]:
        return 0
    values = query.get("pageNo", [])
    if len(values) != 1 or not values[0].isdigit() or int(values[0]) < 1:
        return 0
    return int(values[0])


def _validate_lifelong_pager(
    soup: BeautifulSoup, *, page: int, sentinel: bool
) -> int:
    pagers = soup.select(".wrap_srch_lecture > form#frm > .paging")
    if len(pagers) != 1:
        raise UlleungContractError("lifelong pagination block changed")
    pager = pagers[0]
    numeric_pages: list[int] = []
    for anchor in pager.select(":scope > a"):
        href = _clean(anchor.get("href"))
        if not href:
            if "arrow" not in set(anchor.get("class", [])):
                raise UlleungContractError("lifelong disabled pager control changed")
            continue
        target_page = _lifelong_href_page(anchor)
        if target_page < 1:
            raise UlleungContractError("lifelong pager route escaped canonical 1846")
        numeric_pages.append(target_page)
    focus = pager.select(":scope > strong[title='현재 페이지']")
    if sentinel:
        if focus:
            raise UlleungContractError("lifelong sentinel unexpectedly has page focus")
    elif len(focus) != 1 or _clean(focus[0].get_text(" ", strip=True)) != str(page):
        raise UlleungContractError("lifelong current page focus changed")
    focused_page = 0 if not focus else int(_clean(focus[0].get_text(" ", strip=True)))
    last_nodes = pager.select(":scope > a.arrow.last[title='끝 페이지']")
    if len(last_nodes) != 1:
        raise UlleungContractError("lifelong last-page control changed")
    last_href_page = _lifelong_href_page(last_nodes[0])
    visible = numeric_pages + ([focused_page] if focused_page else [])
    last_page = last_href_page or (max(visible) if visible else 0)
    if last_page < 1:
        raise UlleungContractError("lifelong final page cannot be determined")
    if sentinel:
        if page != last_page + 1:
            raise UlleungContractError("lifelong sentinel is not immediate post-last")
    elif not 1 <= page <= last_page:
        raise UlleungContractError("lifelong requested page exceeds final page")
    if any(value > last_page for value in numeric_pages):
        raise UlleungContractError("lifelong pager links exceed final page")
    return last_page


def _lifelong_event_range(
    value: str, identity: str, title: str
) -> tuple[date, date]:
    try:
        return _date_range(value, identity)
    except UlleungContractError:
        expected = ULLEUNG_LIFELONG_EVENT_CORRECTIONS.get(identity)
        if expected != (title, value):
            raise
        return date(2024, 11, 18), date(2024, 11, 18)


def _lifelong_receipt_range(
    value: str, identity: str, title: str
) -> tuple[date, date, int]:
    match = _LIFELONG_RECEIPT_RE.fullmatch(value)
    if match is not None:
        values = match.groups()
        start = _date_value(*values[:3], identity)
        end = _date_value(*values[4:7], identity)
        if start > end:
            raise UlleungContractError(
                f"lifelong course {identity}: reversed receipt period"
            )
        current, total = int(values[8]), int(values[9])
        if current < 0 or total < 1:
            raise UlleungContractError(
                f"lifelong course {identity}: invalid regular capacity"
            )
        return start, end, total
    expected = ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS.get(identity)
    if expected != (title, value):
        raise UlleungContractError(
            f"lifelong course {identity}: malformed regular receipt period"
        )
    return date(2024, 9, 23), date(2024, 11, 1), 12


def _lifelong_additional_range(
    value: str, identity: str, title: str
) -> tuple[date, date]:
    expected = ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT.get(identity)
    if expected != (title, value):
        raise UlleungContractError(
            f"lifelong course {identity}: unexpected additional receipt period"
        )
    match = _LIFELONG_ADDITIONAL_RE.fullmatch(value)
    if match is None:
        raise UlleungContractError(
            f"lifelong course {identity}: malformed additional receipt period"
        )
    values = match.groups()
    start = _date_value(*values[:3], identity)
    end = _date_value(*values[4:7], identity)
    if start > end:
        raise UlleungContractError(
            f"lifelong course {identity}: reversed additional receipt period"
        )
    return start, end


def _lifelong_item_fields(item: Any, identity: str) -> dict[str, str]:
    output: dict[str, str] = {}
    for node in item.select(":scope > ul.lecture_detail > li"):
        labels = node.select(":scope > span")
        values = node.select(":scope > p")
        if len(labels) != 1 or len(values) != 1:
            raise UlleungContractError(
                f"lifelong course {identity}: malformed list field"
            )
        label = _clean(labels[0].get_text(" ", strip=True))
        value = _clean(values[0].get_text(" ", strip=True))
        if not label or not value or label in output:
            raise UlleungContractError(
                f"lifelong course {identity}: empty or duplicate list field"
            )
        output[label] = value
    expected = (
        ("교육기간", "교육시간", "정규접수", "추가접수", "교육대상", "후보인원")
        if identity in ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT
        else ("교육기간", "교육시간", "정규접수", "교육대상", "후보인원")
    )
    if tuple(output) != expected:
        raise UlleungContractError(
            f"lifelong course {identity}: list field vocabulary changed"
        )
    return output


def _lifelong_key(course: _LifelongCourse) -> tuple[Any, ...]:
    return (
        course.identity,
        course.title,
        course.category,
        course.branch,
        course.source_status,
        course.raw_event_period,
        course.raw_apply_period,
        course.additional_apply_period,
        course.schedule,
        course.target,
        course.capacity_total,
    )


def _parse_lifelong_page(
    soup: BeautifulSoup, *, page: int, sentinel: bool = False
) -> tuple[int, list[_LifelongCourse], str]:
    _validate_lifelong_form(soup)
    last_page = _validate_lifelong_pager(soup, page=page, sentinel=sentinel)
    containers = soup.select(".wrap_srch_lecture > form#frm > .lecture_list")
    if len(containers) != 1:
        raise UlleungContractError("lifelong course list changed")
    items = containers[0].select(":scope > .lecture_item")
    if sentinel:
        expected_valid = len(items) == 0
    elif page < last_page:
        expected_valid = len(items) == ULLEUNG_PAGE_SIZE
    else:
        expected_valid = 1 <= len(items) <= ULLEUNG_PAGE_SIZE
    if not expected_valid:
        raise UlleungContractError(
            f"lifelong page {page}: unexpected course count {len(items)}"
        )
    rows: list[_LifelongCourse] = []
    for item in items:
        top = item.select(":scope > .lecture_top")
        statuses = item.select(":scope > span.lctre_status")
        wrappers = item.select(":scope > .btn_wrap")
        if len(top) != 1 or len(statuses) != 1 or len(wrappers) != 1:
            raise UlleungContractError("lifelong course card structure changed")
        category_nodes = top[0].select(":scope > p.lctre_fld_nm")
        title_nodes = top[0].select(":scope > p.lctre_ttl")
        branch_nodes = top[0].select(":scope > p.site_nm")
        controls = wrappers[0].select(":scope > a[href]")
        if (
            len(category_nodes) != 1
            or len(title_nodes) != 1
            or len(branch_nodes) != 1
            or len(controls) != 1
        ):
            raise UlleungContractError("lifelong course identity fields changed")
        actual_detail = urljoin(ULLEUNG_LIFELONG_URL, _clean(controls[0].get("href")))
        query = _query(actual_detail)
        identity_values = query.get("lctre_uid", [])
        identity = identity_values[0] if len(identity_values) == 1 else ""
        detail_url = ulleung_lifelong_detail_url(identity)
        if not detail_url or not _same_request_url(actual_detail, detail_url):
            raise UlleungContractError("lifelong course detail route changed")
        title = _clean(title_nodes[0].get_text(" ", strip=True))
        category_raw = _clean(category_nodes[0].get_text(" ", strip=True))
        if not title or not category_raw.startswith("[") or not category_raw.endswith("]"):
            raise UlleungContractError(
                f"lifelong course {identity}: title/category changed"
            )
        category = _clean(category_raw[1:-1])
        branch = _clean(branch_nodes[0].get_text(" ", strip=True))
        if category not in ULLEUNG_LIFELONG_CATEGORIES:
            raise UlleungContractError(
                f"lifelong course {identity}: unknown category {category!r}"
            )
        if branch not in ULLEUNG_LIFELONG_BRANCHES:
            raise UlleungContractError(
                f"lifelong course {identity}: unknown owner branch {branch!r}"
            )
        source_status = _clean(statuses[0].get_text(" ", strip=True))
        expected_class = _LIFELONG_STATUS_CLASSES.get(source_status)
        if not expected_class or set(statuses[0].get("class", [])) != {
            "lctre_status",
            expected_class,
        }:
            raise UlleungContractError(
                f"lifelong course {identity}: unknown status contract"
            )
        wrapper_classes = set(wrappers[0].get("class", []))
        control_text = _clean(controls[0].get_text(" ", strip=True))
        if source_status == "접수마감":
            if wrapper_classes != {"btn_wrap", "disabled"} or control_text != "접수마감":
                raise UlleungContractError(
                    f"lifelong course {identity}: closed control changed"
                )
        elif source_status in {"접수예정", "접수대기"}:
            if (
                wrapper_classes != {"btn_wrap", "disabled"}
                or control_text != "수강신청"
            ):
                raise UlleungContractError(
                    f"lifelong course {identity}: scheduled control changed"
                )
        elif wrapper_classes != {"btn_wrap"} or control_text != "수강신청":
            raise UlleungContractError(
                f"lifelong course {identity}: application control changed"
            )
        fields = _lifelong_item_fields(item, identity)
        event_start, event_end = _lifelong_event_range(
            fields["교육기간"], identity, title
        )
        apply_start, apply_end, capacity_total = _lifelong_receipt_range(
            fields["정규접수"], identity, title
        )
        additional = fields.get("추가접수", "")
        if additional:
            additional_start, additional_end = _lifelong_additional_range(
                additional, identity, title
            )
            if additional_start <= apply_end or additional_end < additional_start:
                raise UlleungContractError(
                    f"lifelong course {identity}: additional receipt boundary changed"
                )
            apply_end = additional_end
        target = fields["교육대상"]
        if target not in set(ULLEUNG_LIFELONG_TARGETS.values()):
            raise UlleungContractError(
                f"lifelong course {identity}: unknown target {target!r}"
            )
        rows.append(
            _LifelongCourse(
                identity=identity,
                title=title,
                category=category,
                branch=branch,
                source_status=source_status,
                event_start=event_start,
                event_end=event_end,
                raw_event_period=fields["교육기간"],
                apply_start=apply_start,
                apply_end=apply_end,
                raw_apply_period=fields["정규접수"],
                additional_apply_period=additional,
                schedule=fields["교육시간"],
                target=target,
                capacity_total=capacity_total,
                page=page,
                detail_url=detail_url,
            )
        )
    return last_page, rows, _digest(tuple(_lifelong_key(row) for row in rows))


def _detail_date_range(value: str, identity: str) -> tuple[date, date]:
    match = _LIFELONG_DETAIL_RECEIPT_RE.fullmatch(value)
    if match is None:
        raise UlleungContractError(
            f"lifelong course {identity}: malformed detail receipt period"
        )
    values = match.groups()
    start = _date_value(*values[:3], identity)
    end = _date_value(*values[4:7], identity)
    if start > end:
        raise UlleungContractError(
            f"lifelong course {identity}: reversed detail receipt period"
        )
    return start, end


def _parse_lifelong_detail(
    soup: BeautifulSoup, course: _LifelongCourse
) -> _LifelongDetail:
    roots = soup.select(".es_detail.formStyle")
    if len(roots) != 1:
        raise UlleungContractError(
            f"lifelong course {course.identity}: detail root changed"
        )
    lists = roots[0].select(":scope > dl")
    if len(lists) != 1:
        raise UlleungContractError(
            f"lifelong course {course.identity}: detail definition list changed"
        )
    terms = lists[0].select(":scope > dt")
    values = lists[0].select(":scope > dd")
    labels = tuple(_clean(node.get_text(" ", strip=True)) for node in terms)
    if labels != _LIFELONG_DETAIL_FIELDS or len(values) != len(labels):
        raise UlleungContractError(
            f"lifelong course {course.identity}: detail field vocabulary changed"
        )
    fields = {
        label: _clean(node.get_text(" ", strip=True))
        for label, node in zip(labels, values)
    }
    if fields["교육명"] != course.title:
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail title mismatch"
        )
    receipt_start, receipt_end = _detail_date_range(
        fields["접수 일시"], course.identity
    )
    if (receipt_start, receipt_end) != (course.apply_start, course.apply_end):
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail receipt mismatch"
        )
    event_match = _LIFELONG_DETAIL_EVENT_RE.fullmatch(fields["교육 일시"])
    if event_match is None:
        raise UlleungContractError(
            f"lifelong course {course.identity}: malformed detail event period"
        )
    event_values = event_match.groups()
    detail_event = (
        _date_value(*event_values[:3], course.identity),
        _date_value(*event_values[3:6], course.identity),
    )
    if detail_event != (course.event_start, course.event_end):
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail event mismatch"
        )
    list_schedule = re.fullmatch(r"(.+?)\s*\(\s*([^()]*)\s*\)", course.schedule)
    detail_time = _clean(event_values[6])
    detail_days = fields["교육 요일"]
    if (
        list_schedule is None
        or re.sub(r"\s+", "", list_schedule.group(1))
        != re.sub(r"\s+", "", detail_time)
        or re.sub(r"\s+", "", list_schedule.group(2))
        != re.sub(r"\s+", "", detail_days)
    ):
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail schedule mismatch"
        )
    if fields["교육대상"] != course.target:
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail target mismatch"
        )
    capacity_match = _LIFELONG_DETAIL_CAPACITY_RE.fullmatch(fields["모집인원"])
    if capacity_match is None or int(capacity_match.group(1)) != course.capacity_total:
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail capacity mismatch"
        )
    info = soup.select("#page_info > ul.dataOffer")
    if len(info) != 1:
        raise UlleungContractError(
            f"lifelong course {course.identity}: data-offer owner block changed"
        )
    info_rows: list[tuple[str, str]] = []
    for item in info[0].select(":scope > li"):
        labels_in_row = item.select(":scope > span")
        if len(labels_in_row) != 1:
            raise UlleungContractError(
                f"lifelong course {course.identity}: malformed data-offer row"
            )
        label = _clean(labels_in_row[0].get_text(" ", strip=True))
        whole = _clean(item.get_text(" ", strip=True))
        value = _clean(whole[len(label) :].lstrip(" :"))
        info_rows.append((label, value))
    if tuple(label for label, _ in info_rows) != ("담당부서", "담당자", "전화번호"):
        raise UlleungContractError(
            f"lifelong course {course.identity}: data-offer vocabulary changed"
        )
    if info_rows[0][1] != course.branch:
        raise UlleungContractError(
            f"lifelong course {course.identity}: list/detail branch mismatch"
        )
    board = soup.select(".es_detail.formStyle + .boardBtn")
    if len(board) != 1:
        raise UlleungContractError(
            f"lifelong course {course.identity}: detail controls changed"
        )
    controls = board[0].select(":scope > a")
    if len(controls) != 2:
        raise UlleungContractError(
            f"lifelong course {course.identity}: detail control count changed"
        )
    action, back = controls
    back_url = urljoin(ULLEUNG_LIFELONG_URL, _clean(back.get("href")))
    if (
        set(back.get("class", [])) != {"bt1", "can"}
        or _clean(back.get_text(" ", strip=True)) != "목록"
        or not _safe_https_url(
            back_url, host=ULLEUNG_LIFELONG_HOST, path=ULLEUNG_LIFELONG_PATH
        )
        or _query(back_url) != {"pageNo": [""], "mnu_uid": ["1846"]}
    ):
        raise UlleungContractError(
            f"lifelong course {course.identity}: canonical list return changed"
        )
    action_text = _clean(action.get_text(" ", strip=True))
    if course.source_status == "접수마감":
        if (
            set(action.get("class", [])) != {"grayBtn", "deadline", "big"}
            or action_text != "접수마감"
            or action.has_attr("href")
        ):
            raise UlleungContractError(
                f"lifelong course {course.identity}: closed detail control changed"
            )
        control = "closed"
    elif course.source_status in {"접수예정", "접수대기"}:
        if (
            set(action.get("class", [])) != {"grayBtn", "deadline", "big"}
            or action_text != course.source_status
            or action.has_attr("href")
        ):
            raise UlleungContractError(
                f"lifelong course {course.identity}: scheduled detail control changed"
            )
        control = "scheduled"
    else:
        action_url = urljoin(course.detail_url, _clean(action.get("href")))
        if (
            set(action.get("class", [])) != {"bt2", "btn_blue"}
            or action_text != "신청하기"
            or not _safe_https_url(
                action_url,
                host=ULLEUNG_LIFELONG_HOST,
                path=ULLEUNG_LIFELONG_PATH,
            )
            or _query(action_url)
            != {
                "cmd": ["4"],
                "pageNo": [""],
                "mnu_uid": ["1846"],
                "lctre_uid": [course.identity],
            }
        ):
            raise UlleungContractError(
                f"lifelong course {course.identity}: open detail control changed"
            )
        control = "open"
    schedule = " / ".join(
        value
        for value in (
            f"{detail_time} ({detail_days})",
            fields["1회 교육시간"],
            f"총 {fields['교육횟수']}회" if fields["교육횟수"] else "",
        )
        if value
    )
    return _LifelongDetail(
        venue=fields["장소"],
        schedule=schedule,
        target=fields["교육대상"],
        capacity_total=int(capacity_match.group(1)),
        fee=fields["수강료"],
        material_fee=fields["재료비"],
        control=control,
    )


def _lifelong_effective_status(course: _LifelongCourse, audit_date: date) -> str:
    if course.source_status in {"접수예정", "접수대기"}:
        return "SCHEDULED"
    if (
        course.source_status == "접수중"
        and course.apply_start <= audit_date <= course.apply_end
    ):
        return "OPEN"
    return "CLOSED"


def _lifelong_output(
    target: Any,
    course: _LifelongCourse,
    detail: _LifelongDetail,
    audit_date: date,
) -> dict[str, Any]:
    status = _lifelong_effective_status(course, audit_date)
    if status == "OPEN" and detail.control != "open":
        raise UlleungContractError(
            f"lifelong course {course.identity}: open state/control mismatch"
        )
    if status == "SCHEDULED" and detail.control not in {"scheduled", "closed"}:
        raise UlleungContractError(
            f"lifelong course {course.identity}: scheduled state/control mismatch"
        )
    if status == "CLOSED" and detail.control not in {"closed", "open"}:
        raise UlleungContractError(
            f"lifelong course {course.identity}: closed state/control mismatch"
        )
    application_url = course.detail_url if status == "OPEN" else ""
    output: dict[str, Any] = {
        "provider": ULLEUNG_LIFELONG_PROVIDER,
        "provider_course_id": (
            f"{ULLEUNG_LIFELONG_PROVIDER}:education:lecture:{course.identity}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": course.title,
        "branch": course.branch,
        "branch_code": _branch_code(ULLEUNG_LIFELONG_PROVIDER, course.branch),
        "preserve_branch": True,
        "branch_url": ULLEUNG_LIFELONG_URL,
        "raw_url": course.detail_url,
        "application_url": application_url,
        "application_type": (
            "ONLINE_APPLICATION" if application_url else "INFORMATION_ONLY"
        ),
        "application_method_raw": (
            "울릉군 평생교육원 온라인 신청" if application_url else "정보 제공"
        ),
        "reservation_available": bool(application_url),
        "status": status,
        "period": f"{course.event_start.isoformat()} ~ {course.event_end.isoformat()}",
        "start_date": course.event_start.isoformat(),
        "end_date": course.event_end.isoformat(),
        "apply_period": f"{course.apply_start.isoformat()} ~ {course.apply_end.isoformat()}",
        "apply_start_date": course.apply_start.isoformat(),
        "apply_end_date": course.apply_end.isoformat(),
        "schedule_raw": detail.schedule,
        "target": detail.target,
        "capacity": f"{detail.capacity_total}명",
        "capacity_total": detail.capacity_total,
        "fee": detail.fee,
        "material_fee": detail.material_fee,
        "venue_name": detail.venue,
        "room": detail.venue,
        "category": course.category,
        "collection_type": ULLEUNG_LIFELONG_PARSER,
        "raw_fields": {
            "parser": ULLEUNG_LIFELONG_PARSER,
            "source_kind": "county_lifelong_course",
            "source_identity": course.identity,
            "source_page": course.page,
            "source_branch": course.branch,
            "source_status": course.source_status,
            "source_event_period": course.raw_event_period,
            "source_apply_period": course.raw_apply_period,
            "source_additional_apply_period": course.additional_apply_period,
            "detail_verified": True,
            "application_control_present": detail.control == "open",
            "application_endpoint_fetched": False,
            "service_family": "education",
        },
    }
    output.update(_base_output(target))
    return output


def _empty_family_meta() -> dict[str, Any]:
    return {
        "municipality_code": ULLEUNG_MUNICIPALITY_CODE,
        "municipality_full_name": ULLEUNG_MUNICIPALITY_NAME,
        "provider": ULLEUNG_FAMILY_PROVIDER,
        "canonical_url": ULLEUNG_FAMILY_URL,
        "provider_url_sha1": ULLEUNG_FAMILY_URL_SHA1,
        "canonical_url_sha256": ULLEUNG_FAMILY_URL_SHA256,
        "canonical_candidate_id": ULLEUNG_FAMILY_CANDIDATE_ID,
        "candidate_decisions": dict(ULLEUNG_CANDIDATE_DECISIONS),
        "owner_boundaries": dict(ULLEUNG_OWNER_BOUNDARIES),
        "ledger_totals": {"current_all": 0, "active": 0},
        "ledger_pages": {"current_all": 0, "active": 0},
        "source_total": 0,
        "source_unique_total": 0,
        "default_active_total": 0,
        "source_status_counts": {},
        "branch_counts": {},
        "current_count": 0,
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_ajax_fetches": 0,
        "application_endpoint_fetches": 0,
        "online_application_count": 0,
        "list_requests": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "sentinel_mode": "immediate_empty_post_last_page",
        "sentinel_pages": {"current_all": 0, "active": 0},
        "sentinel_counts": {"current_all": 0, "active": 0},
        "stable_rechecks": {
            f"{scope}_{boundary}": False
            for scope in ("current_all", "active")
            for boundary in ("first", "final", "sentinel")
        },
        "pagination_complete": False,
        "active_reconciled": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pii_payload_persisted": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def _empty_lifelong_meta() -> dict[str, Any]:
    return {
        "municipality_code": ULLEUNG_MUNICIPALITY_CODE,
        "municipality_full_name": ULLEUNG_MUNICIPALITY_NAME,
        "provider": ULLEUNG_LIFELONG_PROVIDER,
        "canonical_url": ULLEUNG_LIFELONG_URL,
        "provider_url_sha1": ULLEUNG_LIFELONG_URL_SHA1,
        "canonical_url_sha256": ULLEUNG_LIFELONG_URL_SHA256,
        "canonical_candidate_id": ULLEUNG_LIFELONG_CANDIDATE_ID,
        "duplicate_alias_provider": ULLEUNG_LIFELONG_ALIAS_PROVIDER,
        "duplicate_alias_url": ULLEUNG_LIFELONG_ALIAS_URL,
        "duplicate_alias_normalized_url": ULLEUNG_LIFELONG_ALIAS_NORMALIZED_URL,
        "duplicate_alias_url_sha1": ULLEUNG_LIFELONG_ALIAS_URL_SHA1,
        "duplicate_alias_url_sha256": ULLEUNG_LIFELONG_ALIAS_URL_SHA256,
        "duplicate_alias_normalized_sha1": ULLEUNG_LIFELONG_ALIAS_NORMALIZED_SHA1,
        "duplicate_alias_normalized_sha256": (
            ULLEUNG_LIFELONG_ALIAS_NORMALIZED_SHA256
        ),
        "duplicate_alias_candidate_id": ULLEUNG_LIFELONG_ALIAS_CANDIDATE_ID,
        "duplicate_alias_decision": ULLEUNG_LIFELONG_ALIAS_DECISION,
        "candidate_decisions": dict(ULLEUNG_CANDIDATE_DECISIONS),
        "owner_boundaries": dict(ULLEUNG_OWNER_BOUNDARIES),
        "ledger_pages": 0,
        "source_total": 0,
        "source_unique_total": 0,
        "source_status_counts": {},
        "branch_counts": {},
        "category_counts": {},
        "target_counts": {},
        "current_count": 0,
        "current_ids": [],
        "expired_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "application_endpoint_fetches": 0,
        "online_application_count": 0,
        "event_correction_ids": [],
        "receipt_correction_ids": [],
        "additional_receipt_ids": [],
        "list_requests": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "sentinel_mode": "immediate_empty_post_last_page",
        "sentinel_page": 0,
        "sentinel_count": 0,
        "stable_rechecks": {"first": False, "final": False, "sentinel": False},
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "pii_payload_persisted": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def _validate_collection_limits(
    *,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    max_workers: int,
    today: Optional[date | datetime | str],
) -> tuple[int, int, int, int, date]:
    timeout_value = int(timeout)
    allowed_pages = int(max_pages)
    allowed_details = int(detail_limit)
    workers = int(max_workers)
    audit_date = _today(today)
    if (
        timeout_value < 1
        or allowed_pages < 1
        or allowed_details < 0
        or not 1 <= workers <= 16
    ):
        raise ValueError
    return timeout_value, allowed_pages, allowed_details, workers, audit_date


def collect_ulleung_family_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 20,
    max_workers: int = ULLEUNG_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete audited current/future Family Center snapshot."""

    meta = _empty_family_meta()
    try:
        (
            timeout_value,
            allowed_pages,
            allowed_details,
            workers,
            audit_date,
        ) = _validate_collection_limits(
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            max_workers=max_workers,
            today=today,
        )
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], ULLEUNG_FAMILY_PARSER, meta
    if not is_ulleung_family_target(target):
        meta["configured_collection_error"] = (
            "target is outside canonical Ulleung Family Center scope"
        )
        return [], ULLEUNG_FAMILY_PARSER, meta

    main_session = session_factory()
    try:
        def request_list(scope: str, page: int) -> BeautifulSoup:
            soup, _, attempts = _request_soup(
                main_session,
                ulleung_family_list_url(audit_date, page, scope=scope),
                timeout=timeout_value,
                fetcher=fetcher,
            )
            meta["logical_requests"] += 1
            meta["physical_requests"] += attempts
            meta["list_requests"] += 1
            return soup

        def traverse(scope: str) -> list[_FamilyProgram]:
            first_soup = request_list(scope, 1)
            total, last_page, first_rows, first_signature = _parse_family_page(
                first_soup,
                scope=scope,
                page=1,
                audit_date=audit_date,
            )
            if last_page > allowed_pages:
                meta["source_cap_reached"] = True
                raise UlleungContractError(
                    f"max_pages cap {allowed_pages} is below {scope} final page "
                    f"{last_page}"
                )
            meta["ledger_totals"][scope] = total
            meta["ledger_pages"][scope] = last_page
            pages: dict[int, list[_FamilyProgram]] = {1: first_rows}
            signatures: dict[int, str] = {1: first_signature}
            for page in range(2, last_page + 1):
                soup = request_list(scope, page)
                page_total, page_last, rows, signature = _parse_family_page(
                    soup,
                    scope=scope,
                    page=page,
                    audit_date=audit_date,
                )
                if (page_total, page_last) != (total, last_page):
                    raise UlleungContractError(
                        f"family {scope}: total/final page changed during traversal"
                    )
                pages[page], signatures[page] = rows, signature
            rows = [row for page in range(1, last_page + 1) for row in pages[page]]
            identities = [int(row.identity) for row in rows]
            if (
                len(rows) != total
                or len(identities) != len(set(identities))
                or identities != sorted(identities, reverse=True)
            ):
                raise UlleungContractError(
                    f"family {scope}: total, uniqueness, or source order changed"
                )

            sentinel_page = last_page + 1
            sentinel_soup = request_list(scope, sentinel_page)
            sentinel_total, sentinel_last, sentinel_rows, sentinel_signature = (
                _parse_family_page(
                    sentinel_soup,
                    scope=scope,
                    page=sentinel_page,
                    audit_date=audit_date,
                    sentinel=True,
                )
            )
            if (
                (sentinel_total, sentinel_last) != (total, last_page)
                or sentinel_rows
                or sentinel_signature != _digest(())
            ):
                raise UlleungContractError(
                    f"family {scope}: immediate post-last sentinel is not empty"
                )
            meta["sentinel_pages"][scope] = sentinel_page
            meta["sentinel_counts"][scope] = len(sentinel_rows)

            recheck_first = request_list(scope, 1)
            rt, rl, _, signature = _parse_family_page(
                recheck_first,
                scope=scope,
                page=1,
                audit_date=audit_date,
            )
            stable = (rt, rl, signature) == (
                total,
                last_page,
                signatures[1],
            )
            meta["stable_rechecks"][f"{scope}_first"] = stable
            if not stable:
                raise UlleungContractError(
                    f"family {scope}: first page changed on recheck"
                )
            if last_page == 1:
                meta["stable_rechecks"][f"{scope}_final"] = True
            else:
                recheck_final = request_list(scope, last_page)
                rt, rl, _, signature = _parse_family_page(
                    recheck_final,
                    scope=scope,
                    page=last_page,
                    audit_date=audit_date,
                )
                stable = (rt, rl, signature) == (
                    total,
                    last_page,
                    signatures[last_page],
                )
                meta["stable_rechecks"][f"{scope}_final"] = stable
                if not stable:
                    raise UlleungContractError(
                        f"family {scope}: final page changed on recheck"
                    )
            recheck_sentinel = request_list(scope, sentinel_page)
            rt, rl, re_rows, signature = _parse_family_page(
                recheck_sentinel,
                scope=scope,
                page=sentinel_page,
                audit_date=audit_date,
                sentinel=True,
            )
            stable = (
                (rt, rl) == (total, last_page)
                and not re_rows
                and signature == sentinel_signature
            )
            meta["stable_rechecks"][f"{scope}_sentinel"] = stable
            if not stable:
                raise UlleungContractError(
                    f"family {scope}: sentinel changed on recheck"
                )
            return rows

        current_rows = traverse("current_all")
        active_rows = traverse("active")
        current_by_id = {row.identity: row for row in current_rows}
        active_by_id = {row.identity: row for row in active_rows}
        expected_active = {
            row.identity: row for row in current_rows if row.source_status == "접수중"
        }
        if set(active_by_id) != set(expected_active):
            raise UlleungContractError(
                "family default-active identities do not reconcile with all-status scope"
            )
        for identity, active in active_by_id.items():
            if _family_contract_key(active) != _family_contract_key(
                current_by_id[identity]
            ):
                raise UlleungContractError(
                    f"family programme {identity}: active/all-status payload mismatch"
                )
        meta["active_reconciled"] = True
        meta["source_total"] = len(current_rows)
        meta["source_unique_total"] = len(current_by_id)
        meta["default_active_total"] = len(active_rows)
        meta["source_status_counts"] = dict(
            sorted(Counter(row.source_status for row in current_rows).items())
        )
        meta["branch_counts"] = {"울릉군 가족센터": len(current_rows)}
        meta["expired_count"] = sum(
            row.event_end < audit_date for row in current_rows
        )
        if len(current_rows) > allowed_details:
            meta["source_cap_reached"] = True
            raise UlleungContractError(
                f"detail_limit cap {allowed_details} is below family current count "
                f"{len(current_rows)}"
            )

        def fetch_detail(program: _FamilyProgram) -> tuple[_FamilyProgram, int]:
            session = session_factory()
            try:
                soup, _, attempts = _request_soup(
                    session,
                    program.detail_url,
                    timeout=timeout_value,
                    fetcher=fetcher,
                )
                _parse_family_detail_shell(soup, program)
                return program, attempts
            finally:
                _close_quietly(session)

        detail_results: list[tuple[_FamilyProgram, int]] = []
        if current_rows:
            if workers == 1:
                detail_results = [fetch_detail(row) for row in current_rows]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(current_rows))
                ) as executor:
                    detail_results = list(executor.map(fetch_detail, current_rows))
        meta["detail_attempts"] = len(current_rows)
        meta["detail_pages"] = len(detail_results)
        meta["logical_requests"] += len(detail_results)
        meta["physical_requests"] += sum(item[1] for item in detail_results)
        results = [
            _family_output(target, program, audit_date)
            for program, _ in detail_results
        ]
        ids = [_clean(row.get("provider_course_id")) for row in results]
        if len(ids) != len(set(ids)) or any(not value for value in ids):
            raise UlleungContractError(
                "family output contains duplicate or empty provider_course_id"
            )
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(results))
            deduped_ids = [_clean(row.get("provider_course_id")) for row in deduped]
            if len(deduped) != len(results) or set(deduped_ids) != set(ids):
                raise UlleungContractError(
                    "external dedupe changed complete family identity snapshot"
                )
            results = deduped
        if len(results) != len(current_rows) or not _privacy_valid(results):
            raise UlleungContractError(
                "family output count or privacy allowlist validation failed"
            )
        meta["current_count"] = len(results)
        meta["online_application_count"] = sum(
            row["application_type"] == "ONLINE_APPLICATION" for row in results
        )
        meta["pagination_complete"] = True
        meta["details_complete"] = meta["detail_pages"] == len(current_rows)
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["active_reconciled"]
            and meta["details_complete"]
            and all(meta["stable_rechecks"].values())
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        meta["request_retry_count"] = (
            meta["physical_requests"] - meta["logical_requests"]
        )
        return results, ULLEUNG_FAMILY_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        meta["request_retry_count"] = max(
            0, meta["physical_requests"] - meta["logical_requests"]
        )
        return [], ULLEUNG_FAMILY_PARSER, meta
    finally:
        _close_quietly(main_session)


def collect_ulleung_lifelong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 20,
    detail_limit: int = 20,
    max_workers: int = ULLEUNG_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the canonical complete mnu_uid=1846 lifelong snapshot."""

    meta = _empty_lifelong_meta()
    try:
        (
            timeout_value,
            allowed_pages,
            allowed_details,
            workers,
            audit_date,
        ) = _validate_collection_limits(
            timeout=timeout,
            max_pages=max_pages,
            detail_limit=detail_limit,
            max_workers=max_workers,
            today=today,
        )
    except (TypeError, ValueError):
        meta["configured_collection_error"] = (
            "timeout/max_pages/detail_limit/max_workers/today are invalid"
        )
        return [], ULLEUNG_LIFELONG_PARSER, meta
    if not is_ulleung_lifelong_target(target):
        meta["configured_collection_error"] = (
            "target is outside canonical Ulleung lifelong mnu_uid=1846 scope"
        )
        return [], ULLEUNG_LIFELONG_PARSER, meta

    main_session = session_factory()
    try:
        def request_list(page: int) -> BeautifulSoup:
            soup, _, attempts = _request_soup(
                main_session,
                ulleung_lifelong_list_url(page),
                timeout=timeout_value,
                fetcher=fetcher,
            )
            meta["logical_requests"] += 1
            meta["physical_requests"] += attempts
            meta["list_requests"] += 1
            return soup

        first_soup = request_list(1)
        last_page, first_rows, first_signature = _parse_lifelong_page(
            first_soup, page=1
        )
        if last_page > allowed_pages:
            meta["source_cap_reached"] = True
            raise UlleungContractError(
                f"max_pages cap {allowed_pages} is below lifelong final page "
                f"{last_page}"
            )
        meta["ledger_pages"] = last_page
        pages: dict[int, list[_LifelongCourse]] = {1: first_rows}
        signatures: dict[int, str] = {1: first_signature}
        for page in range(2, last_page + 1):
            soup = request_list(page)
            page_last, rows, signature = _parse_lifelong_page(soup, page=page)
            if page_last != last_page:
                raise UlleungContractError(
                    "lifelong final page changed during traversal"
                )
            pages[page], signatures[page] = rows, signature
        all_rows = [
            row for page in range(1, last_page + 1) for row in pages[page]
        ]
        identities = [int(row.identity) for row in all_rows]
        if (
            not identities
            or len(identities) != len(set(identities))
            or identities != sorted(identities, reverse=True)
        ):
            raise UlleungContractError(
                "lifelong identities are empty, duplicate, or not descending"
            )
        sentinel_page = last_page + 1
        sentinel_soup = request_list(sentinel_page)
        sentinel_last, sentinel_rows, sentinel_signature = _parse_lifelong_page(
            sentinel_soup, page=sentinel_page, sentinel=True
        )
        if (
            sentinel_last != last_page
            or sentinel_rows
            or sentinel_signature != _digest(())
        ):
            raise UlleungContractError(
                "lifelong immediate post-last sentinel is not empty"
            )
        meta["sentinel_page"] = sentinel_page
        meta["sentinel_count"] = len(sentinel_rows)

        recheck_first = request_list(1)
        rt, _, signature = _parse_lifelong_page(recheck_first, page=1)
        stable = (rt, signature) == (last_page, signatures[1])
        meta["stable_rechecks"]["first"] = stable
        if not stable:
            raise UlleungContractError("lifelong first page changed on recheck")
        if last_page == 1:
            meta["stable_rechecks"]["final"] = True
        else:
            recheck_final = request_list(last_page)
            rt, _, signature = _parse_lifelong_page(
                recheck_final, page=last_page
            )
            stable = (rt, signature) == (
                last_page,
                signatures[last_page],
            )
            meta["stable_rechecks"]["final"] = stable
            if not stable:
                raise UlleungContractError("lifelong final page changed on recheck")
        recheck_sentinel = request_list(sentinel_page)
        rt, re_rows, signature = _parse_lifelong_page(
            recheck_sentinel, page=sentinel_page, sentinel=True
        )
        stable = (
            rt == last_page
            and not re_rows
            and signature == sentinel_signature
        )
        meta["stable_rechecks"]["sentinel"] = stable
        if not stable:
            raise UlleungContractError("lifelong sentinel changed on recheck")

        event_corrections = [
            row.identity
            for row in all_rows
            if row.identity in ULLEUNG_LIFELONG_EVENT_CORRECTIONS
        ]
        receipt_corrections = [
            row.identity
            for row in all_rows
            if row.identity in ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS
        ]
        additional_receipts = [
            row.identity
            for row in all_rows
            if row.identity in ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT
        ]
        if set(event_corrections) != set(ULLEUNG_LIFELONG_EVENT_CORRECTIONS):
            raise UlleungContractError("lifelong event correction identity set changed")
        if set(receipt_corrections) != set(ULLEUNG_LIFELONG_RECEIPT_CORRECTIONS):
            raise UlleungContractError(
                "lifelong receipt correction identity set changed"
            )
        if set(additional_receipts) != set(ULLEUNG_LIFELONG_ADDITIONAL_RECEIPT):
            raise UlleungContractError(
                "lifelong additional receipt identity set changed"
            )
        meta["source_total"] = len(all_rows)
        meta["source_unique_total"] = len(all_rows)
        meta["source_status_counts"] = dict(
            sorted(Counter(row.source_status for row in all_rows).items())
        )
        meta["branch_counts"] = dict(
            sorted(Counter(row.branch for row in all_rows).items())
        )
        meta["category_counts"] = dict(
            sorted(Counter(row.category for row in all_rows).items())
        )
        meta["target_counts"] = dict(
            sorted(Counter(row.target for row in all_rows).items())
        )
        meta["event_correction_ids"] = sorted(event_corrections, key=int, reverse=True)
        meta["receipt_correction_ids"] = sorted(
            receipt_corrections, key=int, reverse=True
        )
        meta["additional_receipt_ids"] = sorted(
            additional_receipts, key=int, reverse=True
        )
        current_rows = [row for row in all_rows if row.event_end >= audit_date]
        meta["current_ids"] = [row.identity for row in current_rows]
        meta["expired_count"] = len(all_rows) - len(current_rows)
        if len(current_rows) > allowed_details:
            meta["source_cap_reached"] = True
            raise UlleungContractError(
                f"detail_limit cap {allowed_details} is below lifelong current count "
                f"{len(current_rows)}"
            )

        def fetch_detail(
            course: _LifelongCourse,
        ) -> tuple[_LifelongCourse, _LifelongDetail, int]:
            session = session_factory()
            try:
                soup, _, attempts = _request_soup(
                    session,
                    course.detail_url,
                    timeout=timeout_value,
                    fetcher=fetcher,
                )
                return course, _parse_lifelong_detail(soup, course), attempts
            finally:
                _close_quietly(session)

        detail_results: list[tuple[_LifelongCourse, _LifelongDetail, int]] = []
        if current_rows:
            if workers == 1:
                detail_results = [fetch_detail(row) for row in current_rows]
            else:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(current_rows))
                ) as executor:
                    detail_results = list(executor.map(fetch_detail, current_rows))
        meta["detail_attempts"] = len(current_rows)
        meta["detail_pages"] = len(detail_results)
        meta["logical_requests"] += len(detail_results)
        meta["physical_requests"] += sum(item[2] for item in detail_results)
        results = [
            _lifelong_output(target, course, detail, audit_date)
            for course, detail, _ in detail_results
        ]
        ids = [_clean(row.get("provider_course_id")) for row in results]
        if len(ids) != len(set(ids)) or any(not value for value in ids):
            raise UlleungContractError(
                "lifelong output contains duplicate or empty provider_course_id"
            )
        if dedupe_rows is not None:
            deduped = list(dedupe_rows(results))
            deduped_ids = [_clean(row.get("provider_course_id")) for row in deduped]
            if len(deduped) != len(results) or set(deduped_ids) != set(ids):
                raise UlleungContractError(
                    "external dedupe changed complete lifelong identity snapshot"
                )
            results = deduped
        if len(results) != len(current_rows) or not _privacy_valid(results):
            raise UlleungContractError(
                "lifelong output count or privacy allowlist validation failed"
            )
        meta["current_count"] = len(results)
        meta["online_application_count"] = sum(
            row["application_type"] == "ONLINE_APPLICATION" for row in results
        )
        meta["pagination_complete"] = True
        meta["details_complete"] = meta["detail_pages"] == len(current_rows)
        meta["snapshot_complete"] = bool(
            meta["pagination_complete"]
            and meta["details_complete"]
            and all(meta["stable_rechecks"].values())
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        meta["request_retry_count"] = (
            meta["physical_requests"] - meta["logical_requests"]
        )
        return results, ULLEUNG_LIFELONG_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        meta["request_retry_count"] = max(
            0, meta["physical_requests"] - meta["logical_requests"]
        )
        return [], ULLEUNG_LIFELONG_PARSER, meta
    finally:
        _close_quietly(main_session)


def collect_ulleung_education(
    target: Any, **kwargs: Any
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if _provider(target) == ULLEUNG_FAMILY_PROVIDER:
        return collect_ulleung_family_education(target, **kwargs)
    return collect_ulleung_lifelong_education(target, **kwargs)


collect_ulleung_family = collect_ulleung_family_education
collect_ulleung_lifelong = collect_ulleung_lifelong_education
collect = collect_ulleung_education
