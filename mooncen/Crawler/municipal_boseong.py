"""Fail-closed collector for Boseong Library's official course catalogues.

The discovery candidate for Boseong points at one old news article.  That
article is not a catalogue.  The same official library owns two independent
``lecture.es`` catalogues: lifelong-learning courses and reading/culture
courses.  This collector retains the discovered provider identity, retargets
it to the canonical lifelong catalogue, and proves the exact union of both
catalogues before returning a snapshot.

Every numbered page, the immediate empty sentinel, and a stable page-one
recheck are required for both sources.  Every current/future list row is then
verified against its detail page, including dates, capacity, status and the
login-gated application control.  Applicant payloads, remarks, contacts,
instructors, attachments, timetable documents and source HTML are never
persisted.  Any ownership, pagination, schema, identity, status, control or
PII drift invalidates the whole snapshot.

The county's separate static information-education page remains owned by
``MUNI_WWW_BOSEONG_GO_KR_3A38AD03``.  The Beolgyo library, Damyang library,
the provincial JNTLE aggregate, and the library's locker reservation route
are also outside this provider boundary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


BOSEONG_PROVIDER = "MUNI_BSLIB_JNE_GO_KR_34227E33"
BOSEONG_CANDIDATE_ID = "MUNI_IR_3C19B6EDDFB4"
BOSEONG_MUNICIPALITY_CODE = "1275000000"
BOSEONG_MUNICIPALITY_NAME = "전남광주통합특별시 보성군"
BOSEONG_BRANCH = "전남광주통합특별시교육청보성도서관"
BOSEONG_HOST = "bslib.jne.go.kr"
BOSEONG_PATH = "/lecture.es"
BOSEONG_PAGE_SIZE = 100
BOSEONG_MAX_HTML_BYTES = 3_000_000
BOSEONG_FETCH_ATTEMPTS = 2
BOSEONG_TLS_CIPHER = "AES256-GCM-SHA384"
BOSEONG_LOGIN_PATH = "/login_search.es"
BOSEONG_LOGIN_SID = "a8"
BOSEONG_CANONICAL_URL = (
    f"https://{BOSEONG_HOST}{BOSEONG_PATH}?mid=a80402000000"
)
BOSEONG_READING_URL = (
    f"https://{BOSEONG_HOST}{BOSEONG_PATH}?mid=a80202000000"
)
BOSEONG_REGISTERED_NOTICE_URL = (
    "https://bslib.jne.go.kr/board.es?"
    "mid=a80701000000&bid=0105&act=view&list_no=3631"
)
BOSEONG_PARSER = (
    "boseong_library_two_complete_lecture_catalogues+empty_sentinels+"
    "stable_page1_rechecks+current_detail_status_capacity+login_gate+"
    "pii_allowlist"
)

# Independently owned or non-course routes found during the municipality-wide
# audit.  These constants let the eventual central integration preserve the
# exact owner boundaries without repeating discovery.
BOSEONG_COUNTY_PROVIDER = "MUNI_WWW_BOSEONG_GO_KR_3A38AD03"
BOSEONG_COUNTY_EDUCATION_URL = (
    "https://www.boseong.go.kr/www/life_welfare/education_info/edu_reserve"
)
BOSEONG_COUNTY_BRANCH = "보성군청"
BOSEONG_COUNTY_NOTICE_CANDIDATE_ID = "MUNI_IR_F8E68C42BD70"
BOSEONG_COUNTY_NOTICE_URL = (
    "https://www.boseong.go.kr/www/open_administration/city_news/notice?"
    "idx=1151343&mode=view"
)
BOSEONG_JNTLE_CANDIDATE_ID = "MUNI_IR_9ACD2C9F7D1C"
BOSEONG_JNTLE_AGGREGATE_URL = (
    "https://www.jntle.kr/main/uDamoaLecture/4?queryType=4678"
)
BOSEONG_DAMYANG_CANDIDATE_ID = "MUNI_IR_3809D58CAC0F"
BOSEONG_DAMYANG_PROVIDER = "MUNI_DYLIB_JNE_GO_KR_0EC67D8E"
BOSEONG_DAMYANG_URL = (
    "https://dylib.jne.go.kr/lecture.es?mid=a80402000000&act=view"
)
BOSEONG_BEOLGYO_BRANCH = "전남광주통합특별시교육청벌교도서관"
BOSEONG_BEOLGYO_URL = "https://bglib.jne.go.kr"
BOSEONG_LOCKER_URL = (
    "https://bslib.jne.go.kr/education.es?mid=a80208000000&eid=0090"
)

BOSEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    BOSEONG_CANDIDATE_ID: {
        "decision": "retarget_notice_identity_to_canonical_two_catalogue_owner",
        "provider": BOSEONG_PROVIDER,
        "url": BOSEONG_REGISTERED_NOTICE_URL,
        "canonical_url": BOSEONG_CANONICAL_URL,
        "owner": BOSEONG_PROVIDER,
        "reason": "single notice is an alias; two lecture catalogues are canonical",
    },
    BOSEONG_COUNTY_NOTICE_CANDIDATE_ID: {
        "decision": "exclude_single_county_notice_keep_separate_county_owner",
        "provider": BOSEONG_COUNTY_PROVIDER,
        "url": BOSEONG_COUNTY_NOTICE_URL,
        "canonical_url": BOSEONG_COUNTY_EDUCATION_URL,
        "owner": BOSEONG_COUNTY_PROVIDER,
        "reason": "county information-education tables are a separate municipal owner",
    },
    BOSEONG_JNTLE_CANDIDATE_ID: {
        "decision": "exclude_provincial_aggregate_duplicate_provenance",
        "provider": "MUNI_WWW_JNTLE_KR_26E84D70",
        "url": BOSEONG_JNTLE_AGGREGATE_URL,
        "canonical_url": "",
        "owner": "",
        "reason": "province-wide discovery aggregate is not the Boseong course owner",
    },
    BOSEONG_DAMYANG_CANDIDATE_ID: {
        "decision": "exclude_wrong_municipality_existing_damyang_owner",
        "provider": BOSEONG_DAMYANG_PROVIDER,
        "url": BOSEONG_DAMYANG_URL,
        "canonical_url": "https://dylib.jne.go.kr/lecture.es?mid=a80402000000",
        "owner": BOSEONG_DAMYANG_PROVIDER,
        "reason": "Damyang Library is not a Boseong branch",
    },
}

BOSEONG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BOSEONG_PROVIDER: {
        "decision": "new_separate_library_course_owner",
        "exact_branch": BOSEONG_BRANCH,
        "catalogues": (BOSEONG_CANONICAL_URL, BOSEONG_READING_URL),
        "excluded_alias": BOSEONG_REGISTERED_NOTICE_URL,
    },
    BOSEONG_COUNTY_PROVIDER: {
        "decision": "retain_existing_separate_county_owner",
        "exact_branch": BOSEONG_COUNTY_BRANCH,
        "catalogues": (BOSEONG_COUNTY_EDUCATION_URL,),
        "audited_source_rows": 13,
        "audited_current_rows": 0,
        "audited_cutoff": "2026-07-21",
    },
    "CULTURE_PUBLIC_LIBRARY_DBB63DD339": {
        "decision": "facility_registry_identity_not_course_provider",
        "exact_branch": BOSEONG_BRANCH,
        "catalogues": (),
    },
    "CULTURE_PUBLIC_LIBRARY_D9C3F54967": {
        "decision": "keep_separate_beolgyo_library_facility_owner",
        "exact_branch": BOSEONG_BEOLGYO_BRANCH,
        "catalogues": (BOSEONG_BEOLGYO_URL,),
    },
}

BOSEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "coverage_candidate_count": 4,
    "coverage_eligible_candidate_count": 1,
    "coverage_excluded_candidate_count": 3,
    "coverage_owner_arrays_stale_empty": True,
    "registered_candidate_is_single_notice": True,
    "retained_provider": BOSEONG_PROVIDER,
    "exact_current_library_name": BOSEONG_BRANCH,
    "former_registry_name_is_stale": "전라남도교육청보성도서관",
    "lifelong_total": 213,
    "lifelong_page_counts": [100, 100, 13],
    "lifelong_current_or_future": 10,
    "lifelong_current_status_counts": {"마감": 7, "신청하기": 3},
    "reading_total": 123,
    "reading_page_counts": [100, 23],
    "reading_current_or_future": 0,
    "cross_catalogue_identity_overlap": 0,
    "historical_application_date_anomalies_quarantined": 2,
    "required_list_requests": 9,
    "county_static_source_rows": 13,
    "county_static_current_or_future": 0,
    "locker_total_non_course_rows": 196,
    "conclusion": (
        "promote the two Boseong Library lecture catalogues under the retained "
        "candidate provider; retain the county page separately and exclude "
        "notices, lockers, the provincial aggregate and other libraries"
    ),
}

BOSEONG_PII_FIELDS_DISCARDED = (
    "비고",
    "강사/담당자",
    "전화번호/이메일",
    "강의 계획서",
    "교육 일정표",
    "첨부파일 및 다운로드 URL",
    "로그인 정보",
    "신청 form payload",
    "신청자 개인 정보",
    "source HTML",
)


@dataclass(frozen=True)
class BoseongSource:
    code: str
    mid: str
    url: str
    menu: str
    program_type: str


BOSEONG_SOURCES: tuple[BoseongSource, ...] = (
    BoseongSource(
        "lifelong",
        "a80402000000",
        BOSEONG_CANONICAL_URL,
        "평생학습",
        "평생학습 강좌",
    ),
    BoseongSource(
        "reading_culture",
        "a80202000000",
        BOSEONG_READING_URL,
        "독서문화진흥",
        "독서문화 강좌",
    ),
)
_SOURCE_BY_CODE = {source.code: source for source in BOSEONG_SOURCES}


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_CAPACITY_RE = re.compile(
    r"^(\d{1,7})\s*/\s*(\d{1,7})\s*"
    r"\(\s*(\d{1,7})\s*/\s*(\d{1,7})\s*\)$"
)
_DETAIL_CAPACITY_RE = re.compile(
    r"^(\d{1,7})\s*명\s*\(\s*대기\s*(\d{1,7})\s*명\s*\)$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LIST_HEADERS = (
    "연번",
    "강좌명",
    "대상",
    "운영기간",
    "인터넷접수",
    "신청 / 정원 (대기인원)",
    "상태",
)
_LIST_ARIA_LABELS = (
    "",
    "강좌명",
    "대상",
    "운영기간",
    "인터넷접수",
    "신청현황",
    "상태",
)
_DETAIL_FIELDS = (
    "강좌명",
    "분기",
    "대상",
    "신청기간",
    "운영기간",
    "강의 시간",
    "회차",
    "강의 요일",
    "교육장소",
    "계좌제 여부",
    "모집인원",
    "신청자",
    "신청방법",
    "접수상태",
    "강의 계획서",
    "교육 일정표",
    "비고",
)
_STATUS_MAP: Mapping[str, str] = {
    "신청하기": "OPEN",
    "마감": "CLOSED",
}
_SAFE_RAW_FIELDS = frozenset(
    {
        "source_catalogue",
        "source_sequence",
        "source_identity",
        "source_status",
        "list_schema_verified",
        "detail_schema_verified",
        "list_detail_verified",
        "capacity_verified",
        "application_control_verified",
        "login_gate_verified",
    }
)
_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "비고",
        "강사",
        "강사명",
        "담당자",
        "전화번호",
        "이메일",
        "강의 계획서",
        "교육 일정표",
        "첨부파일",
        "download_url",
        "attachment",
        "instructor",
        "remarks",
        "detail_pairs",
        "form_payload",
        "source_html",
    }
)


class BoseongContractError(ValueError):
    """Raised when an audited Boseong source contract changes."""


class BoseongTlsAdapter(HTTPAdapter):
    """Verified TLS 1.2 static-RSA compatibility for ``*.jne.go.kr``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.ssl_context = build_boseong_tls_context()
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)


def build_boseong_tls_context() -> ssl.SSLContext:
    """Return the narrow, CA/hostname-verified context required by JNE."""

    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(BOSEONG_TLS_CIPHER)
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS defaults unexpectedly unavailable")
    return context


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _query_map(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(url)
    query: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in query:
            raise BoseongContractError("duplicate URL query parameter")
        query[key] = value
    return parsed, query


def _canonical_target_url(value: Any) -> str:
    raw = _clean(value)
    try:
        parsed, query = _query_map(raw)
        port = parsed.port
    except (ValueError, BoseongContractError):
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname != BOSEONG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != BOSEONG_PATH
        or parsed.params
        or parsed.fragment
        or query != {"mid": BOSEONG_SOURCES[0].mid}
    ):
        return ""
    return BOSEONG_CANONICAL_URL


def is_boseong_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == BOSEONG_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
        == BOSEONG_CANONICAL_URL
    )


is_target = is_boseong_target


def boseong_list_url(source_code: str, page: int = 1) -> str:
    source = _SOURCE_BY_CODE.get(_clean(source_code))
    if source is None:
        raise ValueError(f"unknown Boseong source {source_code!r}")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query = [("mid", source.mid)]
    if page > 1:
        query.append(("nPage", str(page)))
    return f"https://{BOSEONG_HOST}{BOSEONG_PATH}?{urlencode(query)}"


def boseong_detail_url(source_code: str, identity: str) -> str:
    source = _SOURCE_BY_CODE.get(_clean(source_code))
    if source is None:
        raise ValueError(f"unknown Boseong source {source_code!r}")
    identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("identity must be a positive integer")
    return f"https://{BOSEONG_HOST}{BOSEONG_PATH}?" + urlencode(
        (("mid", source.mid), ("act", "view"), ("el_seq", identity))
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.mount("https://", BoseongTlsAdapter())
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": BOSEONG_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise BoseongContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise BoseongContractError("redirect response is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise BoseongContractError("empty HTTP response")
    if len(content) > BOSEONG_MAX_HTML_BYTES:
        raise BoseongContractError("HTTP response exceeded HTML byte cap")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        content = value
    elif isinstance(value, str):
        content = value.encode("utf-8")
    else:
        content = getattr(value, "content", None)
        if content is None:
            text = getattr(value, "text", None)
            content = text.encode("utf-8") if isinstance(text, str) else None
        if content is None:
            raise TypeError("fetcher returned neither HTML nor response")
    if not content:
        raise BoseongContractError("empty HTML")
    if len(content) > BOSEONG_MAX_HTML_BYTES:
        raise BoseongContractError("HTML exceeded byte cap")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _Client:
    def __init__(
        self,
        *,
        timeout: int,
        fetcher: Fetcher,
        session_factory: SessionFactory,
    ) -> None:
        self.timeout = timeout
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.requests = 0
        self.sessions_created = 0

    def get(self, url: str) -> BeautifulSoup:
        last_error: Optional[Exception] = None
        for _attempt in range(BOSEONG_FETCH_ATTEMPTS):
            session: Any = None
            try:
                session = self.session_factory()
                self.sessions_created += 1
                self.requests += 1
                return _coerce_soup(self.fetcher(session, url, self.timeout))
            except Exception as exc:
                last_error = exc
            finally:
                _close_quietly(session)
        assert last_error is not None
        raise last_error


def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise BoseongContractError(f"{label} changed")
    return nodes[0]


def _table_headers(table: Any) -> tuple[str, ...]:
    rows = table.select("thead tr")
    if len(rows) != 1:
        return ()
    return tuple(
        _clean(node.get_text(" ", strip=True))
        for node in rows[0].find_all(["th", "td"], recursive=False)
    )


def _validate_page_contract(
    source: BoseongSource,
    soup: BeautifulSoup,
    page: int,
) -> Any:
    title = _one(soup.select("head > title"), f"{source.code} document title")
    expected_title = f"글쓰기 | 수강 신청 | {source.menu} : {BOSEONG_BRANCH}"
    if _clean(title.get_text(" ", strip=True)) != expected_title:
        raise BoseongContractError(f"{source.code} exact institution title changed")

    form = _one(
        soup.select("form[name='srhForm']"), f"{source.code} catalogue search form"
    )
    if _clean(form.get("method")).lower() != "post":
        raise BoseongContractError(f"{source.code} search form method changed")
    action = urljoin(source.url, _clean(form.get("action")))
    try:
        parsed, query = _query_map(action)
    except BoseongContractError as exc:
        raise BoseongContractError(f"{source.code} search action changed") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != BOSEONG_HOST
        or parsed.path != BOSEONG_PATH
        or query != {"mid": source.mid}
    ):
        raise BoseongContractError(f"{source.code} search action changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type='hidden'][name]")
    }
    expected_hidden = {
        "actionUrl": BOSEONG_PATH,
        "nPage": "" if page == 1 else str(page),
        "mid": source.mid,
        "act": "list",
        "b_list": str(BOSEONG_PAGE_SIZE),
    }
    if hidden != expected_hidden:
        raise BoseongContractError(f"{source.code} pagination form changed")
    keyword = _one(
        form.select("input[name='keyWord']"), f"{source.code} search keyword"
    )
    if _clean(keyword.get("value")):
        raise BoseongContractError(f"{source.code} search filter unexpectedly active")

    table = _one(
        soup.select("table.tstyle_list"), f"{source.code} catalogue table"
    )
    if _table_headers(table) != _LIST_HEADERS:
        raise BoseongContractError(f"{source.code} catalogue headers changed")
    return table


def _date_range(value: Any, label: str) -> tuple[str, str]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise BoseongContractError(f"{label} date range changed")
    try:
        start = date(*(int(part) for part in matches[0]))
        end = date(*(int(part) for part in matches[1]))
    except ValueError as exc:
        raise BoseongContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BoseongContractError(f"{label} date range is reversed")
    return start.isoformat(), end.isoformat()


def _validate_detail_href(
    source: BoseongSource,
    href: Any,
    *,
    page: int,
) -> tuple[str, str]:
    absolute = urljoin(source.url, _clean(href))
    try:
        parsed, query = _query_map(absolute)
        port = parsed.port
    except (ValueError, BoseongContractError) as exc:
        raise BoseongContractError("malformed course detail URL") from exc
    expected_keys = {"mid", "act", "el_seq", "nPage"}
    if (
        parsed.scheme != "https"
        or parsed.hostname != BOSEONG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != BOSEONG_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != expected_keys
        or query.get("mid") != source.mid
        or query.get("act") != "view"
        or query.get("nPage") != ("" if page == 1 else str(page))
        or not _IDENTITY_RE.fullmatch(query.get("el_seq", ""))
    ):
        raise BoseongContractError("course detail URL escaped its catalogue")
    identity = query["el_seq"]
    return identity, boseong_detail_url(source.code, identity)


def _validate_login_control(node: Any, identity: str) -> None:
    if node.name != "a" or _clean(node.get("href")) != "#":
        raise BoseongContractError(f"course {identity} login control href changed")
    if _clean(node.get("onclick")) != "checkLogin(); return false;":
        raise BoseongContractError(f"course {identity} login control changed")
    span = _one(node.select(":scope > span.w_app"), f"course {identity} action label")
    if _clean(span.get_text(" ", strip=True)) != "신청하기":
        raise BoseongContractError(f"course {identity} action label changed")


def _parse_list_page(
    source: BoseongSource,
    soup: BeautifulSoup,
    page: int,
) -> tuple[list[dict[str, Any]], bool]:
    table = _validate_page_contract(source, soup, page)
    rows: list[dict[str, Any]] = []
    explicit_empty = False
    for tr in table.select("tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        number = _clean(cells[0].get_text(" ", strip=True))
        if not number.isdigit():
            text = _clean(tr.get_text(" ", strip=True))
            if (
                len(cells) == 1
                and cells[0].get("class") == ["nodata"]
                and _clean(cells[0].get("colspan")) == "6"
                and text == "등록된 자료가 존재하지 않습니다."
            ):
                explicit_empty = True
                continue
            raise BoseongContractError(f"{source.code} non-numeric source sequence")
        if len(cells) != len(_LIST_HEADERS):
            raise BoseongContractError(f"{source.code} row {number} cell count changed")
        labels = tuple(_clean(cell.get("aria-label")) for cell in cells)
        if labels != _LIST_ARIA_LABELS:
            raise BoseongContractError(f"{source.code} row {number} labels changed")
        title_links = cells[1].select(":scope > a[href]")
        detail_anchor = _one(
            title_links, f"{source.code} row {number} detail anchor"
        )
        identity, raw_url = _validate_detail_href(
            source, detail_anchor.get("href"), page=page
        )
        title = _clean(detail_anchor.get_text(" ", strip=True))
        if not title or _normalized(cells[1].get_text(" ", strip=True)) != _normalized(title):
            raise BoseongContractError(f"course {identity} title contract changed")

        target = _clean(cells[2].get_text(" ", strip=True))
        raw_period = _clean(cells[3].get_text(" ", strip=True))
        raw_apply = _clean(cells[4].get_text(" ", strip=True))
        start, end = _date_range(raw_period, f"course {identity} operating")
        try:
            apply_start, apply_end = _date_range(
                raw_apply, f"course {identity} application"
            )
            application_date_valid = True
        except BoseongContractError:
            # One or more retired rows contain source typos such as
            # ``20241017``.  They are still needed to prove pagination, but
            # their application dates are neither published nor trusted.
            # A current/future row with the same defect fails below before
            # any detail or output can be published.
            apply_start, apply_end = "", ""
            application_date_valid = False
        capacity_text = _clean(cells[5].get_text(" ", strip=True))
        capacity_match = _CAPACITY_RE.fullmatch(capacity_text)
        if not capacity_match:
            raise BoseongContractError(f"course {identity} capacity changed")
        current, total, wait_current, wait_total = (
            int(value) for value in capacity_match.groups()
        )
        # The historical catalogue legitimately contains over-capacity rows
        # (accepted applicants can exceed the nominal quota).  Preserve the
        # source counts and verify current rows against their details instead
        # of treating overbooking as schema corruption.

        status_text = _clean(cells[6].get_text(" ", strip=True))
        if status_text not in _STATUS_MAP:
            raise BoseongContractError(
                f"course {identity} unknown source status {status_text!r}"
            )
        controls = cells[6].select(":scope > a")
        if status_text == "신청하기":
            control = _one(controls, f"course {identity} list application control")
            _validate_login_control(control, identity)
        elif controls:
            raise BoseongContractError(
                f"course {identity} closed row unexpectedly has an action"
            )
        closed = cells[6].select(":scope > span.w_close")
        if (status_text == "마감") != (len(closed) == 1):
            raise BoseongContractError(f"course {identity} closed marker changed")

        rows.append(
            {
                "source_catalogue": source.code,
                "source_sequence": int(number),
                "identity": identity,
                "title": title,
                "target": target,
                "raw_period": raw_period,
                "start_date": start,
                "end_date": end,
                "raw_apply_period": raw_apply,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "application_date_valid": application_date_valid,
                "capacity_current": current,
                "capacity_total": total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "source_status": status_text,
                "status": _STATUS_MAP[status_text],
                "list_control_verified": True,
                "raw_url": raw_url,
            }
        )
    if rows and explicit_empty:
        raise BoseongContractError(f"{source.code} page mixes rows and empty marker")
    return rows, explicit_empty


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    keys = (
        "source_sequence",
        "identity",
        "title",
        "target",
        "raw_period",
        "start_date",
        "end_date",
        "raw_apply_period",
        "apply_start",
        "apply_end",
        "capacity_current",
        "capacity_total",
        "waitlist_current",
        "waitlist_total",
        "source_status",
        "status",
        "raw_url",
    )
    return tuple(row.get(key) for key in keys)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


def _detail_pairs(table: Any, identity: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    order: list[str] = []
    for tr in table.select("tbody > tr, tr"):
        if tr.find_parent("tr") is not None:
            continue
        nodes = tr.find_all(["th", "td"], recursive=False)
        if not nodes:
            continue
        if len(nodes) % 2 or any(
            node.name != ("th" if index % 2 == 0 else "td")
            for index, node in enumerate(nodes)
        ):
            raise BoseongContractError(f"course {identity} detail pairing changed")
        for index in range(0, len(nodes), 2):
            key = _clean(nodes[index].get_text(" ", strip=True))
            if not key or key in pairs:
                raise BoseongContractError(f"course {identity} duplicate detail field")
            order.append(key)
            pairs[key] = _clean(nodes[index + 1].get_text(" ", strip=True))
    if tuple(order) != _DETAIL_FIELDS:
        raise BoseongContractError(f"course {identity} detail fields changed")
    return pairs


def _validate_detail_form(soup: BeautifulSoup, identity: str) -> None:
    form = _one(
        soup.select("form[name='insForm']"), f"course {identity} detail form"
    )
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != "/lecture.es&act=ins"
    ):
        raise BoseongContractError(f"course {identity} detail form changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type='hidden'][name]")
    }
    if hidden != {"actionUrl": BOSEONG_PATH, "nPage": "", "act": "list"}:
        raise BoseongContractError(f"course {identity} detail form payload changed")


def _validate_login_gate(soup: BeautifulSoup, identity: str) -> None:
    scripts = "\n".join(
        node.get_text("\n", strip=True) for node in soup.select("script")
    )
    matches = re.findall(
        r"function\s+checkLogin\s*\(\s*\)\s*\{(.*?)\}", scripts, re.S
    )
    if len(matches) != 1:
        raise BoseongContractError(f"course {identity} login gate changed")
    body = matches[0]
    alert = re.findall(r"alert\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", body)
    location = re.findall(
        r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", body
    )
    if alert != ["로그인 후 이용할 수 있습니다."] or location != [
        f"{BOSEONG_LOGIN_PATH}?sid={BOSEONG_LOGIN_SID}"
    ]:
        raise BoseongContractError(f"course {identity} login gate destination changed")
    if not re.search(r"return\s+false\s*;", body):
        raise BoseongContractError(f"course {identity} login gate return changed")


def _detail_capacity(value: Any, label: str) -> tuple[int, int]:
    match = _DETAIL_CAPACITY_RE.fullmatch(_clean(value))
    if not match:
        raise BoseongContractError(f"{label} changed")
    return int(match.group(1)), int(match.group(2))


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
        raise BoseongContractError("persisted raw-field allowlist changed")
    lowered = {
        _clean(value).casefold()
        for value in _walk_values(row)
        if isinstance(value, str)
    }
    if lowered & {value.casefold() for value in _FORBIDDEN_PERSISTED_KEYS}:
        raise BoseongContractError("forbidden private/detail field reached output")
    for value in _walk_values(row):
        if isinstance(value, str) and _contains_pii(value):
            raise BoseongContractError("phone/email reached persisted allowlist")
    if row.get("description") != row.get("title"):
        raise BoseongContractError("description must contain title only")
    if bool(row.get("application_url")) != bool(row.get("reservation_available")):
        raise BoseongContractError("application availability is inconsistent")


def _branch_code() -> str:
    digest = hashlib.sha1(BOSEONG_BRANCH.encode("utf-8")).hexdigest()[:12].upper()
    return f"{BOSEONG_PROVIDER}:LIBRARY:{digest}"[:100]


def _parse_detail(
    source: BoseongSource,
    parent: Mapping[str, Any],
    soup: BeautifulSoup,
    target: Any,
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    title_node = _one(soup.select("head > title"), f"course {identity} document title")
    expected_title = f"글쓰기 | 수강 신청 | {source.menu} : {BOSEONG_BRANCH}"
    if _clean(title_node.get_text(" ", strip=True)) != expected_title:
        raise BoseongContractError(f"course {identity} detail owner changed")
    table = _one(
        soup.select("table.tstyle_write"), f"course {identity} detail table"
    )
    pairs = _detail_pairs(table, identity)
    _validate_detail_form(soup, identity)
    _validate_login_gate(soup, identity)

    if _normalized(pairs["강좌명"]) != _normalized(parent.get("title")):
        raise BoseongContractError(f"course {identity} detail/list title mismatch")
    detail_target = _normalized(pairs["대상"])
    list_target = _normalized(parent.get("target"))
    # The list adds an audited birth-month restriction for some children's
    # classes while the detail keeps only the base audience.  Require the
    # detail audience to be an exact prefix and retain the more specific list
    # value; unrelated target drift still fails closed.
    if not detail_target or not list_target.startswith(detail_target):
        raise BoseongContractError(f"course {identity} detail/list target mismatch")
    start, end = _date_range(pairs["운영기간"], f"course {identity} detail operating")
    apply_start, apply_end = _date_range(
        pairs["신청기간"], f"course {identity} detail application"
    )
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise BoseongContractError(f"course {identity} detail/list period mismatch")
    if (apply_start, apply_end) != (
        parent.get("apply_start"),
        parent.get("apply_end"),
    ):
        raise BoseongContractError(
            f"course {identity} detail/list application period mismatch"
        )

    status_text = pairs["접수상태"]
    if status_text not in _STATUS_MAP:
        raise BoseongContractError(f"course {identity} unknown detail status")
    status = _STATUS_MAP[status_text]
    if status != parent.get("status") or status_text != parent.get("source_status"):
        raise BoseongContractError(f"course {identity} detail/list status mismatch")
    total, wait_total = _detail_capacity(
        pairs["모집인원"], f"course {identity} detail capacity"
    )
    current, wait_current = _detail_capacity(
        pairs["신청자"], f"course {identity} detail applicants"
    )
    if (current, total, wait_current, wait_total) != (
        parent.get("capacity_current"),
        parent.get("capacity_total"),
        parent.get("waitlist_current"),
        parent.get("waitlist_total"),
    ):
        raise BoseongContractError(f"course {identity} detail/list capacity mismatch")
    if pairs["신청방법"] != "인터넷":
        raise BoseongContractError(f"course {identity} application method changed")

    schedule = _clean(f"{pairs['강의 요일']} {pairs['강의 시간']}")
    if not schedule or _normalized(schedule) not in _normalized(parent.get("raw_period")):
        raise BoseongContractError(f"course {identity} detail/list schedule mismatch")
    venue = pairs["교육장소"]
    if not venue:
        raise BoseongContractError(f"course {identity} venue is empty")

    action_spans = table.select("span.w_app")
    if status == "OPEN":
        span = _one(action_spans, f"course {identity} detail application control")
        control = span.find_parent("a")
        if control is None:
            raise BoseongContractError(f"course {identity} action parent changed")
        _validate_login_control(control, identity)
    elif action_spans:
        raise BoseongContractError(
            f"course {identity} closed detail unexpectedly has an action"
        )

    open_now = status == "OPEN"
    raw_url = _clean(parent.get("raw_url"))
    row = {
        "provider": BOSEONG_PROVIDER,
        "provider_course_id": f"{BOSEONG_PROVIDER}:{source.code}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": BOSEONG_BRANCH,
        "branch_code": _branch_code(),
        "provider_organizer": BOSEONG_BRANCH,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": status,
        "category": "교육",
        "program_type": source.program_type,
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": schedule,
        "target": _clean(parent.get("target")),
        "room": venue,
        "venue": venue,
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_current": current,
        "capacity_total": total,
        "capacity_remaining": max(0, total - current),
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": open_now,
        "application_url": raw_url if open_now else "",
        "application_type": "ONLINE_RESERVATION" if open_now else "",
        "raw_url": raw_url,
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "source_catalogue": source.code,
            "source_sequence": parent.get("source_sequence"),
            "source_identity": identity,
            "source_status": status_text,
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "application_control_verified": True,
            "login_gate_verified": True,
        },
    }
    _validate_persisted_row(row)
    return row


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "request_count": 0,
        "sessions_created": 0,
        "source_count": len(BOSEONG_SOURCES),
        "source_totals": {},
        "source_page_counts": {},
        "source_current_counts": {},
        "source_expired_counts": {},
        "source_status_counts": {},
        "historical_application_date_anomaly_count": 0,
        "source_rows": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "list_rechecks": 0,
        "sentinel_pages": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "application_control_count": 0,
        "cross_source_duplicate_count": 0,
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
        "exact_branch_name": BOSEONG_BRANCH,
        "configured_collection_error": "",
    }


def _failure(message: str, **updates: Any) -> dict[str, Any]:
    meta = _base_meta()
    meta.update(updates)
    meta["configured_collection_error"] = message
    return meta


def collect_boseong_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Boseong Library snapshot."""

    if not is_boseong_target(target):
        return [], BOSEONG_PARSER, _failure(
            "target does not match the canonical Boseong Library education owner"
        )
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        timeout_value = int(timeout)
        cutoff = _today(today)
        if isinstance(max_pages, bool) or isinstance(detail_limit, bool):
            raise ValueError
        if allowed_pages < 0 or allowed_details < 0 or timeout_value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return [], BOSEONG_PARSER, _failure(
            "max_pages/detail_limit/timeout/today are invalid"
        )

    client = _Client(
        timeout=timeout_value,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _default_session_factory,
    )
    errors: list[str] = []
    source_cap_reached = False
    source_rows: dict[str, list[dict[str, Any]]] = {}
    source_totals: dict[str, int] = {}
    source_page_counts: dict[str, list[int]] = {}
    source_current_counts: dict[str, int] = {}
    source_expired_counts: dict[str, int] = {}
    source_status_counts: dict[str, dict[str, int]] = {}
    first_pages: dict[str, BeautifulSoup] = {}
    first_rows: dict[str, list[dict[str, Any]]] = {}
    data_pages: dict[str, int] = {}
    required_list_requests = 0
    list_requests = 0
    list_rechecks = 0
    sentinel_pages = 0
    detail_attempts = 0
    detail_pages = 0

    if allowed_pages < len(BOSEONG_SOURCES):
        source_cap_reached = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of at least "
            f"{len(BOSEONG_SOURCES)} first-page requests"
        )

    if not errors:
        for source in BOSEONG_SOURCES:
            try:
                soup = client.get(source.url)
                list_requests += 1
                parsed, explicit_empty = _parse_list_page(source, soup, 1)
                if parsed:
                    total = int(parsed[0]["source_sequence"])
                elif explicit_empty:
                    total = 0
                else:
                    raise BoseongContractError(
                        f"{source.code} first page lacks rows and empty marker"
                    )
                first_pages[source.code] = soup
                first_rows[source.code] = parsed
                source_totals[source.code] = total
                data_pages[source.code] = max(1, math.ceil(total / BOSEONG_PAGE_SIZE))
            except Exception as exc:
                errors.append(f"{source.code} first page: {type(exc).__name__}: {exc}")
                break

    if not errors:
        required_list_requests = sum(
            pages + 2 for pages in data_pages.values()
        )
        if required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages} of "
                f"{required_list_requests} required pages/sentinels/rechecks"
            )

    if not errors:
        for source in BOSEONG_SOURCES:
            total = source_totals[source.code]
            pages = data_pages[source.code]
            collected: list[dict[str, Any]] = []
            counts: list[int] = []
            for page in range(1, pages + 1):
                try:
                    if page == 1:
                        parsed = first_rows[source.code]
                    else:
                        soup = client.get(boseong_list_url(source.code, page))
                        list_requests += 1
                        parsed, explicit_empty = _parse_list_page(source, soup, page)
                        if explicit_empty:
                            raise BoseongContractError(
                                f"{source.code} data page {page} is unexpectedly empty"
                            )
                    expected = (
                        min(BOSEONG_PAGE_SIZE, total - (page - 1) * BOSEONG_PAGE_SIZE)
                        if total
                        else 0
                    )
                    if len(parsed) != expected:
                        raise BoseongContractError(
                            f"{source.code} page {page} expected {expected} rows, "
                            f"got {len(parsed)}"
                        )
                    counts.append(len(parsed))
                    collected.extend(parsed)
                except Exception as exc:
                    errors.append(
                        f"{source.code} page {page}: {type(exc).__name__}: {exc}"
                    )
                    break
            if errors:
                break
            try:
                sentinel_page = pages + 1
                sentinel = client.get(
                    boseong_list_url(source.code, sentinel_page)
                )
                list_requests += 1
                sentinel_rows, explicit_empty = _parse_list_page(
                    source, sentinel, sentinel_page
                )
                if sentinel_rows or not explicit_empty:
                    raise BoseongContractError(
                        f"{source.code} immediate sentinel is not explicitly empty"
                    )
                sentinel_pages += 1
                recheck = client.get(source.url)
                list_requests += 1
                rechecked_rows, recheck_empty = _parse_list_page(source, recheck, 1)
                list_rechecks += 1
                if recheck_empty != (not first_rows[source.code]) or _page_signature(
                    rechecked_rows
                ) != _page_signature(first_rows[source.code]):
                    raise BoseongContractError(
                        f"{source.code} page-one recheck changed"
                    )
            except Exception as exc:
                errors.append(
                    f"{source.code} completeness: {type(exc).__name__}: {exc}"
                )
                break

            numbers = [int(row["source_sequence"]) for row in collected]
            identities = [_clean(row.get("identity")) for row in collected]
            if numbers != list(range(total, 0, -1)):
                errors.append(f"{source.code} source numbering is not continuous")
            if len(collected) != total:
                errors.append(
                    f"{source.code} declared total {total} != rows {len(collected)}"
                )
            duplicates = len(identities) - len(set(identities))
            if duplicates:
                errors.append(f"{source.code} has {duplicates} duplicate identities")
            source_rows[source.code] = collected
            source_page_counts[source.code] = counts
            if errors:
                break

    all_rows = [
        row
        for source in BOSEONG_SOURCES
        for row in source_rows.get(source.code, [])
    ]
    identity_sources: dict[str, set[str]] = {}
    for row in all_rows:
        identity_sources.setdefault(_clean(row.get("identity")), set()).add(
            _clean(row.get("source_catalogue"))
        )
    cross_source_duplicates = sum(
        len(sources) - 1 for sources in identity_sources.values() if len(sources) > 1
    )
    if cross_source_duplicates:
        errors.append(
            f"{cross_source_duplicates} course identities overlap across catalogues"
        )

    current_parents: list[dict[str, Any]] = []
    expired_count = 0
    historical_application_date_anomaly_count = 0
    for source in BOSEONG_SOURCES:
        current_for_source = 0
        expired_for_source = 0
        statuses: Counter[str] = Counter()
        for row in source_rows.get(source.code, []):
            statuses[_clean(row.get("source_status"))] += 1
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"course {row.get('identity')} invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
                expired_for_source += 1
                historical_application_date_anomaly_count += int(
                    not row.get("application_date_valid")
                )
            else:
                if not row.get("application_date_valid"):
                    errors.append(
                        f"course {row.get('identity')} current/future application "
                        "date range changed"
                    )
                current_parents.append(row)
                current_for_source += 1
        source_current_counts[source.code] = current_for_source
        source_expired_counts[source.code] = expired_for_source
        source_status_counts[source.code] = dict(statuses)

    if len(current_parents) > allowed_details:
        source_cap_reached = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of "
            f"{len(current_parents)} required current/future details"
        )

    detailed_rows: list[dict[str, Any]] = []
    if not errors:
        for parent in current_parents:
            detail_attempts += 1
            source = _SOURCE_BY_CODE[_clean(parent.get("source_catalogue"))]
            try:
                soup = client.get(_clean(parent.get("raw_url")))
                detailed_rows.append(_parse_detail(source, parent, soup, target))
                detail_pages += 1
            except Exception as exc:
                errors.append(
                    f"course {parent.get('identity')} detail: "
                    f"{type(exc).__name__}: {exc}"
                )
                break

    result: list[dict[str, Any]] = []
    if not errors:
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(detailed_rows))
        if len(result) != len(detailed_rows):
            errors.append(
                f"dedupe changed complete row count {len(detailed_rows)} to "
                f"{len(result)}"
            )
            result = []
        else:
            try:
                for row in result:
                    _validate_persisted_row(row)
            except Exception as exc:
                errors.append(f"persisted row validation: {type(exc).__name__}: {exc}")
                result = []

    result.sort(
        key=lambda row: (
            _clean(row.get("start_date")),
            _clean(row.get("title")),
            _clean(row.get("provider_course_id")),
        )
    )
    duplicate_count = len(detailed_rows) - len(
        {_clean(row.get("provider_course_id")) for row in detailed_rows}
    )
    if duplicate_count and not errors:
        errors.append(f"{duplicate_count} duplicate output identities")
        result = []

    snapshot_complete = not errors
    pagination_complete = bool(
        snapshot_complete
        and list_requests == required_list_requests
        and sentinel_pages == len(BOSEONG_SOURCES)
        and list_rechecks == len(BOSEONG_SOURCES)
        and len(all_rows) == sum(source_totals.values())
    )
    details_complete = bool(
        snapshot_complete
        and detail_attempts == len(current_parents)
        and detail_pages == len(current_parents)
    )
    application_control_count = sum(
        row.get("status") == "OPEN" for row in detailed_rows
    )
    controls_complete = bool(
        details_complete
        and all(
            row.get("raw_fields", {}).get("application_control_verified")
            and row.get("raw_fields", {}).get("login_gate_verified")
            for row in detailed_rows
        )
    )
    partition_union_complete = bool(
        snapshot_complete
        and not cross_source_duplicates
        and len(source_rows) == len(BOSEONG_SOURCES)
    )
    full_snapshot_validated = bool(
        snapshot_complete
        and pagination_complete
        and partition_union_complete
        and details_complete
        and controls_complete
    )
    meta = _base_meta()
    meta.update(
        {
            "pages": client.requests,
            "request_count": client.requests,
            "sessions_created": client.sessions_created,
            "source_totals": source_totals,
            "source_page_counts": source_page_counts,
            "source_current_counts": source_current_counts,
            "source_expired_counts": source_expired_counts,
            "source_status_counts": source_status_counts,
            "historical_application_date_anomaly_count": (
                historical_application_date_anomaly_count
            ),
            "source_rows": len(all_rows),
            "current_count": len(current_parents),
            "expired_count": expired_count,
            "returned_count": len(result),
            "required_list_requests": required_list_requests,
            "list_requests": list_requests,
            "list_rechecks": list_rechecks,
            "sentinel_pages": sentinel_pages,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "application_control_count": application_control_count,
            "cross_source_duplicate_count": cross_source_duplicates,
            "duplicate_count": duplicate_count,
            "pagination_detected": any(
                pages > 1 for pages in data_pages.values()
            ),
            "pagination_complete": pagination_complete,
            "partition_union_complete": partition_union_complete,
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": full_snapshot_validated,
            "source_cap_reached": source_cap_reached,
            "no_current_data": bool(snapshot_complete and not current_parents),
            "no_current_reason": (
                "all rows in both complete Boseong Library catalogues have ended"
                if snapshot_complete and not current_parents
                else ""
            ),
            "configured_collection_error": "; ".join(errors),
        }
    )
    if errors:
        return [], BOSEONG_PARSER, meta
    return result, BOSEONG_PARSER, meta


collect = collect_boseong_education_courses


__all__ = [
    "BOSEONG_BEOLGYO_BRANCH",
    "BOSEONG_BEOLGYO_URL",
    "BOSEONG_BRANCH",
    "BOSEONG_CANDIDATE_AUDIT",
    "BOSEONG_CANDIDATE_ID",
    "BOSEONG_CANONICAL_URL",
    "BOSEONG_COUNTY_BRANCH",
    "BOSEONG_COUNTY_EDUCATION_URL",
    "BOSEONG_COUNTY_PROVIDER",
    "BOSEONG_DISCOVERY_AUDIT",
    "BOSEONG_HOST",
    "BOSEONG_LOCKER_URL",
    "BOSEONG_MUNICIPALITY_CODE",
    "BOSEONG_MUNICIPALITY_NAME",
    "BOSEONG_OWNER_BOUNDARY_AUDIT",
    "BOSEONG_PARSER",
    "BOSEONG_PII_FIELDS_DISCARDED",
    "BOSEONG_PROVIDER",
    "BOSEONG_READING_URL",
    "BOSEONG_REGISTERED_NOTICE_URL",
    "BOSEONG_SOURCES",
    "BoseongContractError",
    "BoseongTlsAdapter",
    "boseong_detail_url",
    "boseong_list_url",
    "build_boseong_tls_context",
    "collect",
    "collect_boseong_education_courses",
    "is_boseong_target",
    "is_target",
]
