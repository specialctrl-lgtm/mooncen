"""Fail-closed collector for Gangwon Goseong-gun education catalogues.

The discovered provider is the county homepage, which is only a navigation
shell.  The actual county-owned education inventory is split over four
official catalogues: the integrated reservation education cards, information
education, youth education, and eup/myeon resident-centre programmes.  This
module keeps the discovered provider identity but always traverses every page
of all four catalogues, an immediately empty post-last page, and a stable
page-one recheck before it emits a snapshot.

Only courses whose education end date is current/future are opened.  Detail
pages are used to verify dates, title, capacity, venue, branch, and the public
course-bound application control.  Application forms, identity checks,
applicant lists, confirmation pages, attachments, and downloads are never
requested.  Instructor/staff names, phone numbers, e-mail addresses,
free-form descriptions, and source HTML are not persisted.

``www.gwgs.go.kr`` currently accepts a single old TLS 1.2 RSA cipher.  The
default session mounts a verified compatibility context only for that exact
HTTPS host prefix; certificate and hostname verification stay enabled and
all other hosts retain Requests' normal adapter.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import math
import re
import ssl
import threading
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


GANGWON_GOSEONG_PROVIDER = "MUNI_WWW_GWGS_GO_KR_AEA8B7F8"
GANGWON_GOSEONG_CANDIDATE_ID = "MUNI_IR_E9E7D392DE2F"
GANGWON_GOSEONG_MUNICIPALITY_CODE = "5182000000"
GANGWON_GOSEONG_MUNICIPALITY_NAME = "강원특별자치도 고성군"
GANGWON_GOSEONG_HOST = "www.gwgs.go.kr"
GANGWON_GOSEONG_CANONICAL_URL = f"https://{GANGWON_GOSEONG_HOST}/"
GANGWON_GOSEONG_GENERAL_APPLICATION_PATH = (
    "/prog/eduDscsnAply/yeyak/sub01_01/write.do"
)
GANGWON_GOSEONG_GENERAL_APPLICATION_ACTION = (
    f"https://{GANGWON_GOSEONG_HOST}{GANGWON_GOSEONG_GENERAL_APPLICATION_PATH}"
)
GANGWON_GOSEONG_PAGE_SIZE = 10
GANGWON_GOSEONG_MAX_WORKERS = 8
GANGWON_GOSEONG_MAX_HTML_BYTES = 2_000_000
GANGWON_GOSEONG_LEGACY_CIPHER = "AES256-GCM-SHA384"
GANGWON_GOSEONG_PARSER = (
    "gangwon_goseong_four_county_education_catalogues+declared_totals+"
    "all_pages+empty_post_last+stable_page1+all_current_future_details+"
    "identity_bound_post_and_course_bound_application_controls_no_form_fetch+"
    "host_scoped_verified_tls+pii_allowlist"
)
GANGWON_GOSEONG_OWNERSHIP_SCOPE = (
    "county_integrated_general_information_youth_and_resident_centre_education"
)


@dataclass(frozen=True)
class GoseongSource:
    code: str
    name: str
    list_path: str
    detail_path: str
    layout: str
    heading: str

    @property
    def list_url(self) -> str:
        return f"https://{GANGWON_GOSEONG_HOST}{self.list_path}"

    def page_url(self, page: int) -> str:
        if self.layout == "cards":
            return self.list_url
        return f"{self.list_url}?{urlencode({'pageIndex': page})}"

    def detail_url(self, identity: str, page: int) -> str:
        return f"https://{GANGWON_GOSEONG_HOST}{self.detail_path}?" + urlencode(
            {"pageIndex": page, "eduNo": identity}
        )


GANGWON_GOSEONG_SOURCES: tuple[GoseongSource, ...] = (
    GoseongSource(
        "general",
        "교육프로그램",
        "/prog/eduDscsn/yeyak/sub01_01/list.do",
        "/prog/eduDscsn/yeyak/sub01_01/view.do",
        "cards",
        "교육프로그램",
    ),
    GoseongSource(
        "information",
        "정보화교육",
        "/prog/infoedu/info/sub03_060101/list.do",
        "/prog/infoedu/info/sub03_060101/view.do",
        "table",
        "정보화교육신청",
    ),
    GoseongSource(
        "youth",
        "청소년교육",
        "/prog/lecCourse/youth/sub03_060401/list.do",
        "/prog/lecCourse/youth/sub03_060401/view.do",
        "table",
        "청소년교육신청",
    ),
    GoseongSource(
        "resident",
        "읍면 주민자치프로그램",
        "/prog/lecCourse/EMD/sub03_060501/list.do",
        "/prog/lecCourse/EMD/sub03_060501/view.do",
        "table",
        "읍면 주민자치프로그램",
    ),
)
_SOURCE_BY_CODE = {source.code: source for source in GANGWON_GOSEONG_SOURCES}

GANGWON_GOSEONG_ALIAS_URLS: tuple[str, ...] = (
    "https://www.gwgs.go.kr/yeyak/index.do",
    *(source.list_url for source in GANGWON_GOSEONG_SOURCES),
)

GANGWON_GOSEONG_SEPARATE_OWNER_URLS: Mapping[str, str] = {
    "gangwon_education_office_goseong_library": (
        "https://lib.gwe.go.kr/gslib/menu/874/lecture-event/list/all"
    ),
    "county_sports_association_programmes": (
        "https://www.gwgs.go.kr/prog/phstrn/phstrn/sub02_040101/list.do"
    ),
}

GANGWON_GOSEONG_EXCLUDED_URLS: Mapping[str, str] = {
    "old_migrated_library_archive": (
        "https://www.gwgs.go.kr/prog/lecCourse/lib/sub03_060301/list.do"
    ),
    "empty_health_programme_catalogue": (
        "https://www.gwgs.go.kr/prog/lecCourse/health/sub05_0301/list.do"
    ),
    "women_hall_static_incomplete_schedule": (
        "https://www.gwgs.go.kr/kor/sub02_010403.do"
    ),
    "after_school_static_application_instructions": (
        "https://www.gwgs.go.kr/youth/sub05_04.do"
    ),
    "tourist_attraction_directory": (
        "https://www.gwgs.go.kr/prog/tursmCn/tour/sub02_01/list.do?"
        "contentTypeCode=TC001"
    ),
}

GANGWON_GOSEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    GANGWON_GOSEONG_CANDIDATE_ID: {
        "provider": GANGWON_GOSEONG_PROVIDER,
        "url": GANGWON_GOSEONG_CANONICAL_URL,
        "decision": "include_homepage_owner_expand_to_four_canonical_catalogues",
        "reason": "homepage is a shell; canonical child catalogues contain the courses",
    }
}

GANGWON_GOSEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "canonical_owner_url": GANGWON_GOSEONG_CANONICAL_URL,
    "source_totals": {
        "general": 138,
        "information": 6,
        "youth": 132,
        "resident": 454,
    },
    "source_pages": {
        "general": 14,
        "information": 1,
        "youth": 14,
        "resident": 46,
    },
    "source_total": 730,
    "unique_source_identities_with_source_prefix": 730,
    "immediate_empty_post_last_pages": 4,
    "current_future_counts": {
        "general": 6,
        "information": 2,
        "youth": 2,
        "resident": 20,
    },
    "current_future_total": 30,
    "current_future_status_counts": {"접수마감": 6, "모집마감": 24},
    "current_future_details_verified": 30,
    "old_library_archive_total": 83,
    "old_library_archive_current": 0,
    "health_catalogue_total": 0,
    "separate_sports_total": 22,
    "separate_education_library_current": 3,
    "conclusion": (
        "collect the four county education catalogues under the homepage owner; "
        "keep provincial education-library and sports catalogues separate"
    ),
}

GANGWON_GOSEONG_PII_FIELDS_DISCARDED: tuple[str, ...] = (
    "강사명/지도자",
    "담당자",
    "문의처/문의전화",
    "첨부파일/다운로드",
    "강좌소개/기타내용",
    "신청자/대기자 명단",
    "신청 및 본인확인 payload",
    "source HTML",
)


class GoseongContractError(ValueError):
    """Raised when a Goseong source no longer matches its audited contract."""


@dataclass
class _ListPage:
    source: str
    requested_page: int
    rows: list[dict[str, Any]]
    total: int
    last_page: int
    empty_marker: bool
    errors: list[str]


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, str, Optional[Mapping[str, str]], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\d)"
)
_TOTAL_RE = re.compile(r"총\s*게시물\s*([\d,]+)\s*개")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[\s().-]*\d{3,4}[\s.-]*\d{4}|"
    r"0\d{1,2}[\s().-]*\d{3,4}[\s.-]*\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_EMPTY_TEXTS = (
    "검색된 내용이 없습니다",
    "등록된 강좌가 없습니다",
    "데이터가 없습니다",
)

_TABLE_HEADERS = (
    "강좌명/강사명",
    "대상",
    "접수기간",
    "교육기간",
    "신청인원/모집인원",
    "시간",
    "상태",
)
_GENERAL_REQUIRED_FIELDS = frozenset(
    {"부서명", "접수기간", "교육기간", "신청/모집인원"}
)
_GENERAL_ALLOWED_FIELDS = _GENERAL_REQUIRED_FIELDS | {"교육시간"}
_GENERAL_STATUS_MAP: Mapping[str, str] = {
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "대기자접수중": "OPEN",
    "접수마감": "CLOSED",
    "폐강": "CANCELLED",
}
_TABLE_STATUS_MAP: Mapping[str, str] = {
    "대기중": "SCHEDULED",
    "모집중": "OPEN",
    "대기자모집중": "OPEN",
    "모집마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "폐강": "CANCELLED",
}
_DETAIL_ALLOWED_LABELS = frozenset(
    {
        "교육시간",
        "교육기간",
        "접수기간",
        "교육대상",
        "강사명",
        "담당자",
        "수강료",
        "수업료",
        "교재비",
        "재료비",
        "실습비",
        "문의처",
        "신청/모집인원",
        "첨부파일",
    }
)
_FORWARD_LABELS = frozenset({"교육정원", "교육대상", "교육장소", "문의전화"})

_ALLOWED_ROW_KEYS = frozenset(
    {
        "provider",
        "provider_course_id",
        "title",
        "branch",
        "branch_code",
        "preserve_branch",
        "category",
        "raw_url",
        "application_url",
        "application_type",
        "reservation_available",
        "status",
        "fee",
        "period",
        "start_date",
        "end_date",
        "apply_period",
        "apply_start_date",
        "apply_end_date",
        "schedule_raw",
        "target",
        "capacity_current",
        "capacity_total",
        "venue_name",
        "program_type",
        "collection_category",
        "domain_category",
        "source_group",
        "operator_type",
        "collection_type",
        "application_method_raw",
        "municipality_code",
        "municipality_name",
        "raw_fields",
    }
)
_ALLOWED_RAW_KEYS = frozenset(
    {
        "parser",
        "source_code",
        "source_identity",
        "source_page",
        "source_status",
        "source_selection",
        "source_department",
        "waiting_current",
        "waiting_total",
        "detail_verified",
        "application_control_present",
        "application_control_contract",
        "application_control_verified",
        "fee_evidence",
        "schedule_evidence",
        "target_evidence",
        "venue_evidence",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "manager",
        "staff",
        "contact",
        "phone",
        "email",
        "attachment",
        "attachments",
        "description",
        "source_html",
        "raw_html",
        "application_payload",
        "applicant",
    }
)


class _LegacyCipherAdapter(HTTPAdapter):
    def __init__(self, context: ssl.SSLContext) -> None:
        self.ssl_context = context
        super().__init__()

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _canonical_public_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
        or parsed.params
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return f"https://{parsed.hostname.rstrip('.').lower()}{parsed.path or '/'}" + (
        f"?{query}" if query else ""
    )


def is_gangwon_goseong_education_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == GANGWON_GOSEONG_PROVIDER
        and _canonical_public_url(_target_value(target, "url"))
        == GANGWON_GOSEONG_CANONICAL_URL
    )


def is_gangwon_goseong_alias_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    return bool(
        compared
        and compared
        in {_canonical_public_url(url) for url in GANGWON_GOSEONG_ALIAS_URLS}
    )


def is_gangwon_goseong_excluded_target(target: Any) -> bool:
    compared = _canonical_public_url(_target_value(target, "url"))
    excluded = tuple(GANGWON_GOSEONG_EXCLUDED_URLS.values()) + tuple(
        GANGWON_GOSEONG_SEPARATE_OWNER_URLS.values()
    )
    return bool(
        compared and compared in {_canonical_public_url(url) for url in excluded}
    )


def is_target(target: Any) -> bool:
    return is_gangwon_goseong_education_target(target)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise ValueError("today must be an ISO date") from exc


def _legacy_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(GANGWON_GOSEONG_LEGACY_CIPHER)
    return context


def configure_gangwon_goseong_verified_session(
    session: requests.Session,
) -> requests.Session:
    """Apply the verified legacy cipher only to the official Goseong host."""

    session.trust_env = False
    session.headers.update({"User-Agent": "mooncen-gangwon-goseong/1.0"})
    session.mount(
        f"https://{GANGWON_GOSEONG_HOST}/",
        _LegacyCipherAdapter(_legacy_ssl_context()),
    )
    return session


def _default_session_factory() -> requests.Session:
    return configure_gangwon_goseong_verified_session(requests.Session())


def _allowed_request(method: str, url: str) -> bool:
    parsed = urlparse(url)
    if (
        method not in {"GET", "POST"}
        or parsed.scheme != "https"
        or (parsed.hostname or "").lower() != GANGWON_GOSEONG_HOST
        or parsed.port
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    list_paths = {source.list_path for source in GANGWON_GOSEONG_SOURCES}
    detail_paths = {source.detail_path for source in GANGWON_GOSEONG_SOURCES}
    return parsed.path in list_paths | detail_paths


def _default_fetcher(
    session: requests.Session,
    method: str,
    url: str,
    data: Optional[Mapping[str, str]],
    timeout: int,
) -> requests.Response:
    if not _allowed_request(method, url):
        raise GoseongContractError("attempted fetch outside audited list/detail routes")
    return session.request(
        method,
        url,
        data=dict(data or {}) if method == "POST" else None,
        timeout=timeout,
        allow_redirects=False,
    )


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    if isinstance(response, str):
        payload = response.encode("utf-8")
        status = 200
        final_url = requested_url
        headers: Mapping[str, Any] = {}
        history: Iterable[Any] = ()
    elif isinstance(response, bytes):
        payload = response
        status = 200
        final_url = requested_url
        headers = {}
        history = ()
    else:
        status = int(getattr(response, "status_code", 0) or 0)
        payload = getattr(response, "content", None)
        if payload is None:
            payload = str(getattr(response, "text", "")).encode("utf-8")
        elif isinstance(payload, str):
            payload = payload.encode("utf-8")
        final_url = _clean(getattr(response, "url", requested_url)) or requested_url
        headers = getattr(response, "headers", {}) or {}
        history = getattr(response, "history", ()) or ()
    if status != 200:
        raise GoseongContractError(f"HTTP {status}")
    if history:
        raise GoseongContractError("redirected response is not allowed")
    requested = _canonical_public_url(requested_url)
    final = _canonical_public_url(final_url)
    if not requested or final != requested:
        raise GoseongContractError("response URL differs from requested URL")
    if not isinstance(payload, (bytes, bytearray)):
        raise GoseongContractError("response body is not bytes")
    if not payload or len(payload) > GANGWON_GOSEONG_MAX_HTML_BYTES:
        raise GoseongContractError("response body size is outside the HTML contract")
    content_type = _clean(headers.get("Content-Type") or headers.get("content-type"))
    if content_type and not any(
        allowed in content_type.lower() for allowed in ("text/html", "application/xhtml")
    ):
        raise GoseongContractError("response content type is not HTML")
    soup = BeautifulSoup(bytes(payload), "html.parser")
    if soup.select_one("h1") and _clean(soup.select_one("h1").get_text(" ", strip=True)) == "에러페이지":
        raise GoseongContractError("official site returned its error page")
    return soup


def _request_data(source: GoseongSource, page: int, identity: str = "") -> Mapping[str, str]:
    if source.layout != "cards":
        return {}
    return {
        "pageIndex": str(page),
        "eduNo": identity,
        "searchDeptcode": "",
        "searchRcptBgngDt": "",
        "searchRcptEndDt": "",
        "searchEduBgngYmd": "",
        "searchEduEndYmd": "",
        "searchKeyword": "",
    }


def _list_request(source: GoseongSource, page: int) -> tuple[str, str, Optional[Mapping[str, str]]]:
    if source.layout == "cards":
        return "POST", source.list_url, _request_data(source, page)
    return "GET", source.page_url(page), None


def _detail_request(
    source: GoseongSource, row: Mapping[str, Any]
) -> tuple[str, str, Optional[Mapping[str, str]]]:
    page = int(row["source_page"])
    identity = _clean(row["identity"])
    if source.layout == "cards":
        return (
            "POST",
            f"https://{GANGWON_GOSEONG_HOST}{source.detail_path}",
            _request_data(source, page, identity),
        )
    return "GET", source.detail_url(identity, page), None


def _dates(value: Any) -> tuple[date, ...]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise GoseongContractError("invalid source date") from exc
    return tuple(result)


def _date_range(value: Any, label: str) -> tuple[str, str]:
    values = _dates(value)
    if len(values) != 2:
        raise GoseongContractError(f"{label} must contain a two-date range")
    # A small number of retained 2022-2025 records have their two official
    # endpoints reversed.  Both dates are still explicit, so normalise their
    # order instead of losing an identity from the declared inventory.  If a
    # reversed record overlaps the crawl date it still enters mandatory detail
    # validation and cannot silently bypass the current/future snapshot.
    start, end = sorted(values)
    return start.isoformat(), end.isoformat()


def _total(soup: BeautifulSoup) -> int:
    match = _TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    if not match:
        raise GoseongContractError("declared total is missing")
    return int(match.group(1).replace(",", ""))


def _page_heading(soup: BeautifulSoup) -> str:
    heading = soup.select_one("h2.page__title")
    return _clean(heading.get_text(" ", strip=True)) if heading else ""


def _form_contract(soup: BeautifulSoup, source: GoseongSource) -> list[str]:
    errors: list[str] = []
    if source.layout == "cards":
        form = soup.select_one("form#searchForm[name=searchForm]")
        required = {
            "pageIndex",
            "eduNo",
            "searchDeptcode",
            "searchRcptBgngDt",
            "searchRcptEndDt",
            "searchEduBgngYmd",
            "searchEduEndYmd",
            "searchKeyword",
        }
    else:
        form = soup.select_one("form[name=eduSearchForm]")
        required = {
            "pageUnit",
            "pageIndex",
            "pageSize",
            "integrDeptCode",
            "searchCtgry",
            "groupYn",
            "state",
            "stDt",
            "edDt",
            "searchCondition",
            "searchKeyword",
        }
        if source.code in {"youth", "resident"}:
            required.add("kind")
    if form is None:
        return ["audited search form is missing"]
    if _clean(form.get("method")).lower() != "post":
        errors.append("search form method changed")
    action = urljoin(source.list_url, _clean(form.get("action")))
    if _canonical_public_url(action) != source.list_url:
        errors.append("search form action changed")
    present = {
        _clean(field.get("name")) for field in form.select("input[name],select[name]")
    }
    missing = required - present
    if missing:
        errors.append("search form fields missing: " + ",".join(sorted(missing)))
    return errors


def _parse_capacity(value: str) -> tuple[int, int, int, int]:
    cleaned = _clean(value)
    match = re.fullmatch(
        r"(\d{1,7})\s*(?:명)?\s*/\s*(\d{1,7})\s*(?:명)?"
        r"(?:\s*\(?(?:대기|대기자)\s*:?\s*\(?(\d{1,7})\s*(?:명)?\s*/\s*(\d{1,7})?\s*(?:명)?\)?\)?)?",
        cleaned,
    )
    if not match:
        raise GoseongContractError("capacity format changed")
    current, total = int(match.group(1)), int(match.group(2))
    waiting_current = int(match.group(3) or 0)
    waiting_total = int(match.group(4) or 0)
    if min(current, total, waiting_current, waiting_total) < 0 or total < 1:
        raise GoseongContractError("capacity values are invalid")
    return current, total, waiting_current, waiting_total


def _general_fields(card: Any) -> Mapping[str, str]:
    fields: dict[str, str] = {}
    for item in card.select("ul.list-1st > li"):
        label = item.select_one(".tit")
        value = item.select_one(".txt")
        if label is None or value is None:
            raise GoseongContractError("general card field structure changed")
        key = _clean(label.get_text(" ", strip=True))
        if not key or key in fields:
            raise GoseongContractError("general card contains duplicate field")
        fields[key] = _clean(value.get_text(" ", strip=True))
    if set(fields) - _GENERAL_ALLOWED_FIELDS:
        raise GoseongContractError("general card field vocabulary changed")
    if not _GENERAL_REQUIRED_FIELDS.issubset(fields):
        raise GoseongContractError("general card required field is missing")
    return fields


def _parse_general_page(
    soup: BeautifulSoup, source: GoseongSource, requested_page: int
) -> _ListPage:
    errors = _form_contract(soup, source)
    if _page_heading(soup) != source.heading:
        errors.append("page heading changed")
    try:
        total = _total(soup)
    except GoseongContractError as exc:
        return _ListPage(source.code, requested_page, [], 0, 0, False, errors + [str(exc)])
    last = max(1, math.ceil(total / GANGWON_GOSEONG_PAGE_SIZE))
    rows: list[dict[str, Any]] = []
    for card in soup.select("div.list-wrap div.item[data-key-no]"):
        try:
            identity = _clean(card.get("data-key-no"))
            if not _IDENTITY_RE.fullmatch(identity):
                raise GoseongContractError("general source identity changed")
            title_node = card.select_one("strong.title")
            status_node = card.select_one(".status-wrap .status")
            selection_node = card.select_one(".status-wrap .type")
            title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
            source_status = (
                _clean(status_node.get_text(" ", strip=True)) if status_node else ""
            )
            if not title or source_status not in _GENERAL_STATUS_MAP:
                raise GoseongContractError("general title/status contract changed")
            fields = _general_fields(card)
            start_date, end_date = _date_range(fields["교육기간"], "education period")
            apply_start, apply_end = _date_range(fields["접수기간"], "application period")
            current, capacity, waiting_current, waiting_total = _parse_capacity(
                fields["신청/모집인원"]
            )
            rows.append(
                {
                    "source": source.code,
                    "identity": identity,
                    "source_page": requested_page,
                    "title": title,
                    "source_status": source_status,
                    "status": _GENERAL_STATUS_MAP[source_status],
                    "source_selection": _clean(
                        selection_node.get_text(" ", strip=True)
                        if selection_node
                        else ""
                    ),
                    "source_department": fields["부서명"],
                    "period": fields["교육기간"],
                    "start_date": start_date,
                    "end_date": end_date,
                    "apply_period": fields["접수기간"],
                    "apply_start_date": apply_start,
                    "apply_end_date": apply_end,
                    "schedule_raw": fields.get("교육시간", ""),
                    "target": "",
                    "capacity_current": current,
                    "capacity_total": capacity,
                    "waiting_current": waiting_current,
                    "waiting_total": waiting_total,
                }
            )
        except GoseongContractError as exc:
            errors.append(f"card: {exc}")
    text = _clean(soup.get_text(" ", strip=True))
    return _ListPage(
        source.code,
        requested_page,
        rows,
        total,
        last,
        any(marker in text for marker in _EMPTY_TEXTS),
        errors,
    )


def _parse_table_page(
    soup: BeautifulSoup, source: GoseongSource, requested_page: int
) -> _ListPage:
    errors = _form_contract(soup, source)
    if _page_heading(soup) != source.heading:
        errors.append("page heading changed")
    try:
        total = _total(soup)
    except GoseongContractError as exc:
        return _ListPage(source.code, requested_page, [], 0, 0, False, errors + [str(exc)])
    last = max(1, math.ceil(total / GANGWON_GOSEONG_PAGE_SIZE))
    table = soup.select_one("table")
    headers = tuple(
        _clean(node.get_text(" ", strip=True)).replace(" ", "")
        for node in (table.select("thead th") if table else [])
    )
    if headers != tuple(value.replace(" ", "") for value in _TABLE_HEADERS):
        errors.append("course table headers changed")
    rows: list[dict[str, Any]] = []
    for tr in table.select("tbody tr") if table else ():
        cells = tr.select("td[data-cell-header]")
        link = tr.select_one('td[data-cell-header] a[href*="eduNo="]')
        if not link:
            continue
        try:
            if len(cells) != 7:
                raise GoseongContractError("course row cell count changed")
            href = urljoin(source.list_url, _clean(link.get("href")))
            parsed = urlparse(href)
            query = parse_qs(parsed.query, keep_blank_values=True)
            identity = _clean((query.get("eduNo") or [""])[0])
            page_value = _clean((query.get("pageIndex") or [""])[0])
            if (
                parsed.scheme != "https"
                or parsed.hostname != GANGWON_GOSEONG_HOST
                or parsed.path != source.detail_path
                or not _IDENTITY_RE.fullmatch(identity)
                or page_value != str(requested_page)
            ):
                raise GoseongContractError("detail link contract changed")
            title = _clean(link.get_text(" ", strip=True))
            source_status = _clean(cells[6].get_text(" ", strip=True))
            if not title or source_status not in _TABLE_STATUS_MAP:
                raise GoseongContractError("table title/status contract changed")
            apply_period = _clean(cells[2].get_text(" ", strip=True))
            period = _clean(cells[3].get_text(" ", strip=True))
            start_date, end_date = _date_range(period, "education period")
            apply_start, apply_end = _date_range(apply_period, "application period")
            current, capacity, waiting_current, waiting_total = _parse_capacity(
                _clean(cells[4].get_text(" ", strip=True))
            )
            rows.append(
                {
                    "source": source.code,
                    "identity": identity,
                    "source_page": requested_page,
                    "title": title,
                    "source_status": source_status,
                    "status": _TABLE_STATUS_MAP[source_status],
                    "source_selection": "",
                    "source_department": "",
                    "period": period,
                    "start_date": start_date,
                    "end_date": end_date,
                    "apply_period": apply_period,
                    "apply_start_date": apply_start,
                    "apply_end_date": apply_end,
                    "schedule_raw": _clean(cells[5].get_text(" ", strip=True)),
                    "target": _clean(cells[1].get_text(" ", strip=True)),
                    "capacity_current": current,
                    "capacity_total": capacity,
                    "waiting_current": waiting_current,
                    "waiting_total": waiting_total,
                }
            )
        except GoseongContractError as exc:
            errors.append(f"row: {exc}")
    text = _clean(soup.get_text(" ", strip=True))
    return _ListPage(
        source.code,
        requested_page,
        rows,
        total,
        last,
        any(marker in text for marker in _EMPTY_TEXTS),
        errors,
    )


def _parse_list_page(
    soup: BeautifulSoup, source: GoseongSource, requested_page: int
) -> _ListPage:
    if source.layout == "cards":
        return _parse_general_page(soup, source, requested_page)
    return _parse_table_page(soup, source, requested_page)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            _clean(row.get("identity")),
            _clean(row.get("title")),
            _clean(row.get("source_status")),
            _clean(row.get("period")),
            _clean(row.get("apply_period")),
        )
        for row in rows
    )


def _detail_fields(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    errors: list[str] = []
    for item in soup.select(".caption-info .li"):
        label_node = item.select_one("b")
        if label_node is None:
            errors.append("detail field label is missing")
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        full = _clean(item.get_text(" ", strip=True))
        value = _clean(full[len(label) :]) if full.startswith(label) else ""
        if not label or label in fields:
            errors.append("detail field label is empty or duplicated")
        elif label not in _DETAIL_ALLOWED_LABELS:
            errors.append(f"unknown detail field: {label}")
        else:
            fields[label] = value
    return fields, errors


def _forward_fields(soup: BeautifulSoup) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    errors: list[str] = []
    for item in soup.select(".apply-article .item"):
        label_node = item.select_one("strong")
        value_node = item.select_one("em")
        if label_node is None or value_node is None:
            errors.append("detail summary item structure changed")
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        value = _clean(value_node.get_text(" ", strip=True))
        if not label or label in fields:
            errors.append("detail summary label is empty or duplicated")
        elif label not in _FORWARD_LABELS:
            errors.append(f"unknown detail summary field: {label}")
        else:
            fields[label] = value
    return fields, errors


def _branch_for(
    source: GoseongSource,
    listed: Mapping[str, Any],
    target: str,
    venue: str,
) -> str:
    title = _clean(listed.get("title"))
    if source.code == "general":
        department = _clean(listed.get("source_department"))
        return {
            "교육행정팀": "고성군청 교육문화과 교육행정팀",
            "평생교육팀": "고성군청 교육문화과 평생교육팀",
        }.get(department, "")
    if source.code == "information":
        return "고성군청 정보화교육장" if "정보화교육장" in venue else ""
    combined = " ".join((title, target, venue))
    if source.code == "youth":
        for token, branch in (
            ("토성청소년문화의집", "토성청소년문화의집"),
            ("거진청소년문화의집", "거진청소년문화의집"),
            ("현내청소년문화의집", "현내청소년문화의집"),
            ("고성청소년수련관", "고성청소년수련관"),
            ("(토성)", "토성청소년문화의집"),
            ("(거진)", "거진청소년문화의집"),
            ("(현내)", "현내청소년문화의집"),
        ):
            if token in combined:
                return branch
        return ""
    if source.code == "resident":
        for token in ("간성읍", "거진읍", "현내면", "죽왕면", "토성면"):
            if token in combined:
                return f"{token} 주민자치센터"
        return ""
    return ""


def _fee(value: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        return ""
    if cleaned in {"0", "0원", "무료"}:
        return "무료"
    if re.fullmatch(r"[\d,]+", cleaned):
        return f"{cleaned}원"
    return cleaned


def _application_path_allowed(source: GoseongSource, path: str) -> bool:
    prefixes = {
        "general": "/prog/eduDscsnAply/yeyak/",
        "information": "/prog/infoedu_reserve/info/",
        "youth": "/prog/lecReserve/youth/",
        "resident": "/prog/lecReserve/EMD/",
    }
    return bool(
        path.startswith(prefixes[source.code])
        and path.endswith(("/write.do", "/insert.do", "/insertView.do"))
    )


def _control_url(element: Any, detail_url: str) -> str:
    href = _clean(element.get("href"))
    if href and href != "#":
        return urljoin(detail_url, href)
    onclick = _clean(element.get("onclick"))
    match = re.search(
        r"(?:location(?:\.href)?|window\.location)\s*=\s*['\"]([^'\"]+)['\"]",
        onclick,
    )
    return urljoin(detail_url, match.group(1)) if match else ""


_GENERAL_POST_HANDLER_RE = re.compile(
    r"""
    \$\(\s*["']\.button_aply["']\s*\)\s*\.click
    \s*\(\s*function\s*\(\s*\)\s*\{
    \s*\$\(\s*["']\#actionForm["']\s*\)\s*\.submit\(\s*\)\s*;?\s*
    \}\s*\)\s*;?
    """,
    re.DOTALL | re.VERBOSE,
)


def _general_post_application_control(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
    detail_url: str,
) -> tuple[bool, str]:
    forms = list(soup.select("form#actionForm"))
    if len(forms) != 1:
        return False, "general POST application form is missing or ambiguous"
    form = forms[0]
    action = urljoin(detail_url, _clean(form.get("action")))
    identities = list(form.select('input[name="eduNo"]'))
    if (
        _clean(form.get("name")) != "actionForm"
        or _clean(form.get("method")).upper() != "POST"
        or action != GANGWON_GOSEONG_GENERAL_APPLICATION_ACTION
        or len(identities) != 1
        or _clean(identities[0].get("value")) != _clean(listed.get("identity"))
    ):
        return False, "general POST application identity or action changed"
    scripts = [
        script.get_text("\n", strip=False)
        for script in soup.find_all("script")
        if "button_aply" in script.get_text("\n", strip=False)
    ]
    matches = sum(
        len(tuple(_GENERAL_POST_HANDLER_RE.finditer(script))) for script in scripts
    )
    if len(scripts) != 1 or matches != 1:
        return False, "general POST application handler is missing or ambiguous"
    return True, ""


def _application_control(
    soup: BeautifulSoup,
    source: GoseongSource,
    listed: Mapping[str, Any],
    detail_url: str,
) -> tuple[str, bool, str, list[str]]:
    errors: list[str] = []
    selector = (
        ".fe-btn_box .box-footer-inner a, .fe-btn_box .box-footer-inner button"
        if source.layout == "cards"
        else ".figure .btn_wrap a, .figure .btn_wrap button"
    )
    elements = list(soup.select(selector))
    active_labels = {"신청하기", "접수하기", "수강신청", "대기자신청"}
    inactive_labels = {
        "접수마감",
        "모집마감",
        "접수대기",
        "대기중",
        "교육종료",
        "폐강",
    }
    active: list[tuple[Any, str, str]] = []
    inactive: list[Any] = []
    for element in elements:
        label = _clean(element.get_text(" ", strip=True))
        candidate = _control_url(element, detail_url)
        control_contract = ""
        if (
            not candidate
            and source.code == "general"
            and element.name == "button"
            and "button_aply" in (element.get("class") or ())
            and label in active_labels
        ):
            post_control, handler_error = _general_post_application_control(
                soup, listed, detail_url
            )
            if handler_error:
                errors.append(handler_error)
            elif post_control:
                candidate = detail_url
                control_contract = "identity_bound_post_control"
        if label in active_labels or (
            candidate and _application_path_allowed(source, urlparse(candidate).path)
        ):
            active.append((element, candidate, control_contract))
        elif label in inactive_labels:
            inactive.append(element)

    status = _clean(listed.get("status"))
    if status == "OPEN":
        if len(active) != 1:
            errors.append("open course must expose exactly one application control")
            return "", bool(active), "missing_or_multiple_active_control", errors
        element, candidate, control_contract = active[0]
        if control_contract:
            if errors:
                return "", True, "invalid_active_control", errors
            return candidate, True, control_contract, errors
        parsed = urlparse(candidate)
        query = parse_qs(parsed.query, keep_blank_values=True)
        identity = _clean((query.get("eduNo") or [""])[0])
        common_invalid = (
            parsed.scheme != "https"
            or parsed.hostname != GANGWON_GOSEONG_HOST
            or parsed.port
            or parsed.username
            or parsed.password
            or parsed.fragment
        )
        course_bound = (
            _application_path_allowed(source, parsed.path)
            and identity == _clean(listed.get("identity"))
        )
        if common_invalid or not course_bound:
            errors.append("application control is not an exact course-bound public route")
            return "", True, "invalid_active_control", errors
        return candidate, True, "course_bound_public_control", errors

    if active:
        errors.append("non-open course exposes an active application route")
    if source.layout == "table":
        if len(inactive) != 1:
            errors.append("non-open table detail must expose one inactive status control")
        else:
            element = inactive[0]
            href = _clean(element.get("href"))
            onclick = _clean(element.get("onclick"))
            if href not in {"", "#"} or "alert" not in onclick:
                errors.append("inactive status control unexpectedly navigates")
    return "", bool(active or inactive), "verified_inactive_control", errors


def _validate_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup
) -> tuple[Optional[dict[str, Any]], list[str]]:
    source = _SOURCE_BY_CODE[_clean(listed.get("source"))]
    identity = _clean(listed.get("identity"))
    detail_url = source.detail_url(identity, int(listed["source_page"]))
    errors: list[str] = []
    if _page_heading(soup) != source.heading:
        errors.append("detail page heading changed")
    title_node = soup.select_one("strong.caption-title")
    detail_title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    if not detail_title or _normalized(detail_title) != _normalized(listed.get("title")):
        errors.append("detail title differs from list")
    fields, field_errors = _detail_fields(soup)
    forward, forward_errors = _forward_fields(soup)
    errors.extend(field_errors)
    errors.extend(forward_errors)
    required = {"교육기간", "접수기간", "교육시간"}
    if source.layout == "cards":
        required |= {"교육대상", "신청/모집인원"}
    else:
        required |= {"수업료"}
        if set(forward) != _FORWARD_LABELS:
            errors.append("detail summary fields are incomplete")
    if not required.issubset(fields):
        errors.append("required detail fields are missing")
    try:
        period_dates = _date_range(fields.get("교육기간", ""), "detail education period")
        apply_dates = _date_range(fields.get("접수기간", ""), "detail application period")
    except GoseongContractError as exc:
        errors.append(str(exc))
        period_dates = ("", "")
        apply_dates = ("", "")
    if period_dates != (listed.get("start_date"), listed.get("end_date")):
        errors.append("detail education period differs from list")
    if apply_dates != (
        listed.get("apply_start_date"),
        listed.get("apply_end_date"),
    ):
        errors.append("detail application period differs from list")
    if _normalized(fields.get("교육시간")) != _normalized(listed.get("schedule_raw")):
        errors.append("detail schedule differs from list")

    target = _clean(
        fields.get("교육대상")
        if source.layout == "cards"
        else forward.get("교육대상")
    )
    listed_target = _clean(listed.get("target"))
    if listed_target and _normalized(target) != _normalized(listed_target):
        errors.append("detail target differs from list")
    venue = _clean(forward.get("교육장소"))
    branch = _branch_for(source, listed, target, venue)
    if not branch:
        errors.append("official branch could not be resolved")
    if source.layout == "cards":
        try:
            _, total, waiting_current, waiting_total = _parse_capacity(
                fields.get("신청/모집인원", "")
            )
            if (
                total != listed.get("capacity_total")
                or waiting_current != listed.get("waiting_current")
                or waiting_total != listed.get("waiting_total")
            ):
                errors.append("detail capacity differs from list")
        except GoseongContractError as exc:
            errors.append(str(exc))
    else:
        match = re.fullmatch(r"(\d{1,7})\s*명", _clean(forward.get("교육정원")))
        if not match or int(match.group(1)) != listed.get("capacity_total"):
            errors.append("detail capacity differs from list")

    application_url, control_present, control_contract, control_errors = (
        _application_control(soup, source, listed, detail_url)
    )
    errors.extend(control_errors)
    if errors:
        return None, errors
    fee_value = fields.get("수강료") if source.layout == "cards" else fields.get("수업료")
    fee = _fee(_clean(fee_value))
    schedule = _clean(listed.get("schedule_raw"))
    evidence: dict[str, str] = {}
    if not target:
        target = "대상 별도 안내"
        evidence["target_evidence"] = "official_detail_omits_target"
    if not venue:
        venue = "장소 별도 안내"
        evidence["venue_evidence"] = "official_detail_omits_venue"
    if not fee:
        fee = "요금 별도 안내"
        evidence["fee_evidence"] = "official_detail_omits_fee"
    if not schedule:
        schedule = "시간 별도 안내"
        evidence["schedule_evidence"] = "official_detail_omits_schedule"
    provider_course_id = f"{source.code}:{identity}"
    row: dict[str, Any] = {
        "provider": GANGWON_GOSEONG_PROVIDER,
        "provider_course_id": provider_course_id,
        "title": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": f"{source.code}:{_normalized(branch)}",
        "preserve_branch": True,
        "category": "교육",
        "raw_url": detail_url,
        "application_url": application_url,
        "application_type": "online" if application_url else "",
        "reservation_available": bool(application_url),
        "status": _clean(listed.get("status")),
        "fee": fee,
        "period": _clean(listed.get("period")),
        "start_date": _clean(listed.get("start_date")),
        "end_date": _clean(listed.get("end_date")),
        "apply_period": _clean(listed.get("apply_period")),
        "apply_start_date": _clean(listed.get("apply_start_date")),
        "apply_end_date": _clean(listed.get("apply_end_date")),
        "schedule_raw": schedule,
        "target": target,
        "capacity_current": int(listed.get("capacity_current") or 0),
        "capacity_total": int(listed.get("capacity_total") or 0),
        "venue_name": venue,
        "program_type": "교육",
        "collection_category": "education",
        "domain_category": "education",
        "source_group": source.code,
        "operator_type": "지자체/공공기관",
        "collection_type": "교육",
        "application_method_raw": "온라인",
        "municipality_code": GANGWON_GOSEONG_MUNICIPALITY_CODE,
        "municipality_name": GANGWON_GOSEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "parser": GANGWON_GOSEONG_PARSER,
            "source_code": source.code,
            "source_identity": identity,
            "source_page": int(listed["source_page"]),
            "source_status": _clean(listed.get("source_status")),
            "source_selection": _clean(listed.get("source_selection")),
            "source_department": _clean(listed.get("source_department")),
            "waiting_current": int(listed.get("waiting_current") or 0),
            "waiting_total": int(listed.get("waiting_total") or 0),
            "detail_verified": True,
            "application_control_present": control_present,
            "application_control_contract": control_contract,
            "application_control_verified": True,
            **evidence,
        },
    }
    privacy_errors = _privacy_errors(row)
    return (None, privacy_errors) if privacy_errors else (row, [])


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    unknown = set(row) - _ALLOWED_ROW_KEYS
    if unknown:
        errors.append("unexpected persisted keys: " + ",".join(sorted(unknown)))
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        errors.append("raw_fields is not a mapping")
        raw = {}
    unknown_raw = set(raw) - _ALLOWED_RAW_KEYS
    if unknown_raw:
        errors.append("unexpected raw_fields keys: " + ",".join(sorted(unknown_raw)))

    def inspect_keys(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                lowered = _clean(key).casefold()
                if any(blocked in lowered for blocked in _FORBIDDEN_PERSISTED_KEYS):
                    errors.append(f"forbidden persisted key: {key}")
                inspect_keys(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                inspect_keys(child)

    inspect_keys(row)
    pii_values = (
        row.get("title"),
        row.get("branch"),
        row.get("target"),
        row.get("venue_name"),
        row.get("schedule_raw"),
        row.get("fee"),
        raw.get("source_department"),
    )
    for value in pii_values:
        text = _clean(value)
        if _PHONE_RE.search(text) or _EMAIL_RE.search(text):
            errors.append("PII-like phone/e-mail leaked into persisted allowlist")
    return list(dict.fromkeys(errors))


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            raise GoseongContractError("duplicate provider course identity")
        seen.add(identity)
        result.append(row)
    return result


def _fetch_parse_many(
    items: Iterable[
        tuple[
            Any,
            str,
            str,
            Optional[Mapping[str, str]],
            Callable[[BeautifulSoup], Any],
        ]
    ],
    *,
    session_factory: SessionFactory,
    fetcher: Fetcher,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, Any], list[str]]:
    item_list = list(items)
    if not item_list:
        return {}, []
    local = threading.local()
    sessions: list[Any] = []
    session_lock = threading.Lock()

    def get_session() -> Any:
        if not hasattr(local, "session"):
            local.session = session_factory()
            with session_lock:
                sessions.append(local.session)
        return local.session

    def run(item: Any) -> tuple[Any, Any]:
        key, method, url, data, parser = item
        if not _allowed_request(method, url):
            raise GoseongContractError("work item is outside audited routes")
        response = fetcher(get_session(), method, url, data, timeout)
        return key, parser(_response_soup(response, url))

    results: dict[Any, Any] = {}
    errors: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(item_list))) as pool:
            futures = {pool.submit(run, item): item[0] for item in item_list}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    parsed_key, value = future.result()
                    results[parsed_key] = value
                except Exception as exc:
                    errors.append(f"{key}: {_clean(exc)}")
    finally:
        for session in sessions:
            close = getattr(session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
    return results, errors


def _base_meta() -> dict[str, Any]:
    return {
        "provider": GANGWON_GOSEONG_PROVIDER,
        "municipality_code": GANGWON_GOSEONG_MUNICIPALITY_CODE,
        "canonical_url": GANGWON_GOSEONG_CANONICAL_URL,
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "configured_collection_error": "",
    }


def collect_gangwon_goseong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 200,
    today: Optional[date | datetime | str] = None,
    max_workers: int = GANGWON_GOSEONG_MAX_WORKERS,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future county education snapshot."""

    meta = _base_meta()
    if not is_gangwon_goseong_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical Gangwon Goseong owner"
        )
        return [], GANGWON_GOSEONG_PARSER, meta
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
                "configured_collection_error": (
                    "invalid timeout/max_pages/detail_limit/max_workers cap"
                ),
            }
        )
        return [], GANGWON_GOSEONG_PARSER, meta
    try:
        cutoff = _today(today)
    except ValueError as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], GANGWON_GOSEONG_PARSER, meta

    factory = session_factory or _default_session_factory
    current_fetcher = fetcher or _default_fetcher
    errors: list[str] = []
    first_items = []
    for source in GANGWON_GOSEONG_SOURCES:
        method, url, data = _list_request(source, 1)
        first_items.append(
            (
                (source.code, 1, "data"),
                method,
                url,
                data,
                lambda soup, current_source=source: _parse_list_page(
                    soup, current_source, 1
                ),
            )
        )
    first_values, fetch_errors = _fetch_parse_many(
        first_items,
        session_factory=factory,
        fetcher=current_fetcher,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(first_values)
    meta["list_requests"] += len(first_values)
    first_pages: dict[str, _ListPage] = {}
    for source in GANGWON_GOSEONG_SOURCES:
        page = first_values.get((source.code, 1, "data"))
        if not isinstance(page, _ListPage):
            errors.append(f"{source.code} page 1 response missing")
            continue
        errors.extend(f"{source.code} page 1: {error}" for error in page.errors)
        first_pages[source.code] = page
    required_list_requests = sum(page.last_page + 2 for page in first_pages.values())
    meta.update(
        {
            "required_list_requests": required_list_requests,
            "declared_totals": {
                code: page.total for code, page in first_pages.items()
            },
            "declared_pages": {
                code: page.last_page for code, page in first_pages.items()
            },
        }
    )
    if len(first_pages) != len(GANGWON_GOSEONG_SOURCES):
        errors.append("one or more source page-one contracts are unavailable")
    if required_list_requests > max_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {max_pages} of {required_list_requests} "
            "required list requests"
        )
    if errors:
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], GANGWON_GOSEONG_PARSER, meta

    remaining_items = []
    for source in GANGWON_GOSEONG_SOURCES:
        first = first_pages[source.code]
        for page_index in range(2, first.last_page + 1):
            method, url, data = _list_request(source, page_index)
            remaining_items.append(
                (
                    (source.code, page_index, "data"),
                    method,
                    url,
                    data,
                    lambda soup, current_source=source, current_page=page_index: _parse_list_page(
                        soup, current_source, current_page
                    ),
                )
            )
        sentinel_page = first.last_page + 1
        method, url, data = _list_request(source, sentinel_page)
        remaining_items.append(
            (
                (source.code, sentinel_page, "sentinel"),
                method,
                url,
                data,
                lambda soup, current_source=source, current_page=sentinel_page: _parse_list_page(
                    soup, current_source, current_page
                ),
            )
        )
        method, url, data = _list_request(source, 1)
        remaining_items.append(
            (
                (source.code, 1, "recheck"),
                method,
                url,
                data,
                lambda soup, current_source=source: _parse_list_page(
                    soup, current_source, 1
                ),
            )
        )
    remaining, fetch_errors = _fetch_parse_many(
        remaining_items,
        session_factory=factory,
        fetcher=current_fetcher,
        timeout=timeout,
        max_workers=max_workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)

    all_rows: list[dict[str, Any]] = []
    page_counts: dict[str, dict[int, int]] = {}
    source_identities: dict[str, list[str]] = {}
    for source in GANGWON_GOSEONG_SOURCES:
        first = first_pages[source.code]
        pages: dict[int, _ListPage] = {1: first}
        source_page_counts: dict[int, int] = {1: len(first.rows)}
        for page_index in range(2, first.last_page + 1):
            value = remaining.get((source.code, page_index, "data"))
            if not isinstance(value, _ListPage):
                errors.append(f"{source.code} page {page_index} response missing")
                continue
            pages[page_index] = value
            source_page_counts[page_index] = len(value.rows)
            errors.extend(
                f"{source.code} page {page_index}: {error}"
                for error in value.errors
            )
            if value.total != first.total or value.last_page != first.last_page:
                errors.append(f"{source.code} pagination declaration changed")
        for page_index in range(1, first.last_page + 1):
            value = pages.get(page_index)
            if value is None:
                continue
            expected = max(
                0,
                min(
                    GANGWON_GOSEONG_PAGE_SIZE,
                    first.total - (page_index - 1) * GANGWON_GOSEONG_PAGE_SIZE,
                ),
            )
            if len(value.rows) != expected:
                errors.append(
                    f"{source.code} page {page_index} has {len(value.rows)} "
                    f"rows, expected {expected}"
                )
            all_rows.extend(value.rows)
        sentinel_key = (source.code, first.last_page + 1, "sentinel")
        sentinel = remaining.get(sentinel_key)
        if not isinstance(sentinel, _ListPage):
            errors.append(f"{source.code} empty sentinel response missing")
        else:
            meta["sentinel_requests"] += 1
            errors.extend(
                f"{source.code} sentinel: {error}" for error in sentinel.errors
            )
            if (
                sentinel.rows
                or sentinel.total != first.total
                or sentinel.last_page != first.last_page
                or (source.layout == "cards" and not sentinel.empty_marker)
            ):
                errors.append(f"{source.code} post-last sentinel changed")
        recheck = remaining.get((source.code, 1, "recheck"))
        if not isinstance(recheck, _ListPage):
            errors.append(f"{source.code} page-one recheck missing")
        else:
            meta["stability_rechecks"] += 1
            errors.extend(
                f"{source.code} recheck: {error}" for error in recheck.errors
            )
            if (
                recheck.total != first.total
                or recheck.last_page != first.last_page
                or _page_signature(recheck.rows) != _page_signature(first.rows)
            ):
                errors.append(f"{source.code} page-one stability recheck changed")
        source_rows = [row for row in all_rows if row["source"] == source.code]
        identities = [_clean(row["identity"]) for row in source_rows]
        source_identities[source.code] = identities
        if len(source_rows) != first.total:
            errors.append(
                f"{source.code} collected {len(source_rows)} of {first.total} rows"
            )
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(
                f"{source.code} contains {duplicate_count} duplicate source identities"
            )
        page_counts[source.code] = source_page_counts

    prefixed_ids = [f"{row['source']}:{row['identity']}" for row in all_rows]
    if len(prefixed_ids) != len(set(prefixed_ids)):
        errors.append("source-prefixed identities are not globally unique")
    list_complete = bool(
        not errors
        and len(all_rows) == sum(page.total for page in first_pages.values())
        and meta["list_requests"] == required_list_requests
        and meta["sentinel_requests"] == len(GANGWON_GOSEONG_SOURCES)
        and meta["stability_rechecks"] == len(GANGWON_GOSEONG_SOURCES)
    )
    current_candidates = [
        row
        for row in all_rows
        if row["status"] != "CANCELLED"
        and date.fromisoformat(row["end_date"]) >= cutoff
    ]
    cancelled_current = [
        row
        for row in all_rows
        if row["status"] == "CANCELLED"
        and date.fromisoformat(row["end_date"]) >= cutoff
    ]
    if len(current_candidates) > detail_limit:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {detail_limit} of "
            f"{len(current_candidates)} required current/future details"
        )

    validated_rows: list[dict[str, Any]] = []
    detail_errors: list[str] = []
    if list_complete and not errors:
        detail_items = []
        for listed in current_candidates:
            source = _SOURCE_BY_CODE[listed["source"]]
            method, url, data = _detail_request(source, listed)
            identity_key = f"{source.code}:{listed['identity']}"
            detail_items.append(
                (
                    identity_key,
                    method,
                    url,
                    data,
                    lambda soup, current_listed=dict(listed): _validate_detail(
                        current_listed, soup
                    ),
                )
            )
        meta["detail_attempts"] = len(detail_items)
        detail_values, detail_fetch_errors = _fetch_parse_many(
            detail_items,
            session_factory=factory,
            fetcher=current_fetcher,
            timeout=timeout,
            max_workers=max_workers,
        )
        detail_errors.extend(detail_fetch_errors)
        meta["pages"] += len(detail_values)
        for listed in current_candidates:
            identity_key = f"{listed['source']}:{listed['identity']}"
            value = detail_values.get(identity_key)
            if not isinstance(value, tuple) or len(value) != 2:
                detail_errors.append(f"{identity_key}: detail response missing")
                continue
            row, item_errors = value
            if item_errors:
                detail_errors.extend(
                    f"{identity_key}: {error}" for error in item_errors
                )
            elif not isinstance(row, dict):
                detail_errors.append(f"{identity_key}: validated detail row missing")
            else:
                validated_rows.append(row)
                meta["detail_pages"] += 1
    errors.extend(detail_errors)
    details_complete = bool(
        list_complete
        and not detail_errors
        and meta["detail_attempts"] == len(current_candidates)
        and meta["detail_pages"] == len(current_candidates)
    )
    application_controls_complete = bool(
        details_complete
        and all(
            row["raw_fields"]["application_control_verified"] is True
            for row in validated_rows
        )
    )

    result: list[dict[str, Any]] = []
    if application_controls_complete and not errors:
        deduper = dedupe_rows or _dedupe_default
        try:
            result = list(deduper(validated_rows))
        except Exception as exc:
            errors.append(f"dedupe failed: {_clean(exc)}")
        before = {_clean(row["provider_course_id"]) for row in validated_rows}
        after = {
            _clean(row.get("provider_course_id"))
            for row in result
            if isinstance(row, Mapping)
        }
        if len(result) != len(validated_rows) or before != after:
            errors.append(
                "dedupe changed official identity cardinality or membership"
            )
            result = []
        else:
            for row in result:
                errors.extend(_privacy_errors(row))
            if errors:
                result = []
    snapshot_complete = bool(
        list_complete
        and details_complete
        and application_controls_complete
        and not errors
    )
    if not snapshot_complete:
        result = []

    raw_identity_counts = Counter(row["identity"] for row in all_rows)
    semantic_counts = Counter(
        (
            _normalized(row["title"]),
            row["start_date"],
            row["end_date"],
            _normalized(row["branch"]),
        )
        for row in validated_rows
    )
    meta.update(
        {
            "ownership_scope": GANGWON_GOSEONG_OWNERSHIP_SCOPE,
            "source_total": len(all_rows),
            "source_rows": len(all_rows),
            "source_totals": {
                code: first_pages[code].total for code in first_pages
            },
            "page_counts": page_counts,
            "source_identity_counts": {
                code: len(values) for code, values in source_identities.items()
            },
            "source_prefixed_unique_identities": len(set(prefixed_ids)),
            "raw_cross_source_identity_collision_count": sum(
                count - 1 for count in raw_identity_counts.values() if count > 1
            ),
            "current_source_count": len(current_candidates),
            "cancelled_current_count": len(cancelled_current),
            "current_source_counts": dict(
                Counter(row["source"] for row in current_candidates)
            ),
            "source_status_counts": dict(
                Counter(row["source_status"] for row in all_rows)
            ),
            "current_source_status_counts": dict(
                Counter(row["source_status"] for row in current_candidates)
            ),
            "branch_counts": dict(Counter(row["branch"] for row in result)),
            "status_counts": dict(Counter(row["status"] for row in result)),
            "public_application_control_count": sum(
                bool(row["application_url"]) for row in result
            ),
            "semantic_duplicate_group_count": sum(
                count > 1 for count in semantic_counts.values()
            ),
            "semantic_duplicate_excess_rows": sum(
                max(0, count - 1) for count in semantic_counts.values()
            ),
            "semantic_duplicate_policy": (
                "preserve_distinct_official_source_prefixed_eduNo"
            ),
            "detail_errors": len(detail_errors),
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "application_controls_complete": application_controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "returned_count": len(result),
            "no_current_data": bool(snapshot_complete and not current_candidates),
            "no_current_reason": (
                "all four complete official catalogues have no unexpired courses"
                if snapshot_complete and not current_candidates
                else ""
            ),
            "municipality_coverage": [GANGWON_GOSEONG_MUNICIPALITY_CODE],
            "candidate_audit": {
                key: dict(value)
                for key, value in GANGWON_GOSEONG_CANDIDATE_AUDIT.items()
            },
            "alias_urls": list(GANGWON_GOSEONG_ALIAS_URLS),
            "separate_owner_urls": dict(GANGWON_GOSEONG_SEPARATE_OWNER_URLS),
            "excluded_urls": dict(GANGWON_GOSEONG_EXCLUDED_URLS),
            "discovery_audit": dict(GANGWON_GOSEONG_DISCOVERY_AUDIT),
            "pii_fields_discarded": list(GANGWON_GOSEONG_PII_FIELDS_DISCARDED),
            "pii_payload_persisted": False,
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, GANGWON_GOSEONG_PARSER, meta


collect = collect_gangwon_goseong_education


__all__ = [
    "GANGWON_GOSEONG_ALIAS_URLS",
    "GANGWON_GOSEONG_CANONICAL_URL",
    "GANGWON_GOSEONG_CANDIDATE_AUDIT",
    "GANGWON_GOSEONG_CANDIDATE_ID",
    "GANGWON_GOSEONG_DISCOVERY_AUDIT",
    "GANGWON_GOSEONG_EXCLUDED_URLS",
    "GANGWON_GOSEONG_GENERAL_APPLICATION_PATH",
    "GANGWON_GOSEONG_GENERAL_APPLICATION_ACTION",
    "GANGWON_GOSEONG_HOST",
    "GANGWON_GOSEONG_LEGACY_CIPHER",
    "GANGWON_GOSEONG_MUNICIPALITY_CODE",
    "GANGWON_GOSEONG_MUNICIPALITY_NAME",
    "GANGWON_GOSEONG_OWNERSHIP_SCOPE",
    "GANGWON_GOSEONG_PAGE_SIZE",
    "GANGWON_GOSEONG_PARSER",
    "GANGWON_GOSEONG_PII_FIELDS_DISCARDED",
    "GANGWON_GOSEONG_PROVIDER",
    "configure_gangwon_goseong_verified_session",
    "GANGWON_GOSEONG_SEPARATE_OWNER_URLS",
    "GANGWON_GOSEONG_SOURCES",
    "GoseongContractError",
    "GoseongSource",
    "collect",
    "collect_gangwon_goseong_education",
    "is_gangwon_goseong_alias_target",
    "is_gangwon_goseong_education_target",
    "is_gangwon_goseong_excluded_target",
    "is_target",
]
