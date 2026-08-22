"""Fail-closed collector for Yeonggwang County's education catalogue.

The discovery candidate is the county home page.  The actual structured
source is the county's ``기관별 평생학습`` board.  It declares a total and
uses 15-row offset pagination; a complete snapshot therefore requires every
declared page plus a stable page-one recheck.

Only current/future rows are detailed and returned.  Course identity, list
state, application window, detail values and the identity-bound login gate
are verified.  The application endpoint is fetched with GET only and forms
are never submitted.  Contact fields, email, attachments, free-form content,
application data, CSRF tokens and source HTML are discarded.

The county library, Office-of-Education library, arts centre and swimming
pool remain separate owners even though historical records or navigation
links occur in the municipal board.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import hashlib
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


YEONGGWANG_PROVIDER = "MUNI_WWW_YEONGGWANG_GO_KR_A3BCC0C3"
YEONGGWANG_CANDIDATE_ID = "MUNI_IR_257521C510BB"
YEONGGWANG_TOUR_CANDIDATE_ID = "MUNI_IR_B37C9077B3E3"
YEONGGWANG_MUNICIPALITY_CODE = "1283000000"
YEONGGWANG_MUNICIPALITY_NAME = "전남광주통합특별시 영광군"
YEONGGWANG_SITE_NAME = "영광군청"
YEONGGWANG_CATALOGUE_NAME = "기관별 평생학습"
YEONGGWANG_CURRENT_BRANCH = "인구교육정책실"
YEONGGWANG_HOST = "www.yeonggwang.go.kr"
YEONGGWANG_PATH = "/bbs/"
YEONGGWANG_SITE = "headquarter_new"
YEONGGWANG_MENU = "9247"
YEONGGWANG_BOARD = "lecture"
YEONGGWANG_PAGE_SIZE = 15
YEONGGWANG_FETCH_ATTEMPTS = 2
YEONGGWANG_MAX_HTML_BYTES = 2_000_000
YEONGGWANG_CANDIDATE_URL = "https://www.yeonggwang.go.kr/"
YEONGGWANG_CANONICAL_URL = (
    "https://www.yeonggwang.go.kr/bbs/?"
    "b_id=lecture&site=headquarter_new&mn=9247"
)
YEONGGWANG_TOUR_CANDIDATE_URL = (
    "https://tour.yeonggwang.go.kr/subpage/?site=tour_2019&mn=7379"
)
YEONGGWANG_PARSER = (
    "yeonggwang_declared_total_complete_offset_pages+stable_page1+"
    "current_detail+application_window+identity_bound_login_gate+pii_allowlist"
)

# Separate owner surfaces found in the catalogue navigation or historical
# municipal records.
YEONGGWANG_COUNTY_LIBRARY_PROVIDER = "CULTURE_PUBLIC_LIBRARY_6BB9A3A9C4"
YEONGGWANG_COUNTY_LIBRARY_BRANCH = "영광군립도서관"
YEONGGWANG_COUNTY_LIBRARY_URL = "https://www.yggunlib.go.kr/CulturalCourse"
YEONGGWANG_EDUCATION_LIBRARY_PROVIDER = "CULTURE_PUBLIC_LIBRARY_B23D89B1D2"
YEONGGWANG_EDUCATION_LIBRARY_BRANCH = (
    "전남광주통합특별시교육청영광도서관"
)
YEONGGWANG_EDUCATION_LIBRARY_RESIDENT_URL = (
    "https://yglib.jne.go.kr/lecture.es?mid=b70402010100"
)
YEONGGWANG_EDUCATION_LIBRARY_STUDENT_URL = (
    "https://yglib.jne.go.kr/lecture.es?mid=b70402010200"
)
YEONGGWANG_ARTS_PROVIDER = "CULTURE_ARTS_CENTER_F79644406E"
YEONGGWANG_ARTS_BRANCH = "영광문화예술의전당"
YEONGGWANG_SWIMMING_PROVIDER = "SPORTS_SPORTS_D93D6390AB"
YEONGGWANG_SWIMMING_STALE_DUPLICATE_PROVIDER = "SPORTS_SPORTS_081436A946"
YEONGGWANG_SWIMMING_BRANCH = "영광실내수영장"
YEONGGWANG_SINHWALLYEOK_URL = (
    "https://www.ygboricenter.or.kr/bbs/board.php?bo_table=table59"
)
YEONGGWANG_SENIOR_URL = (
    "https://www.yeonggwang.go.kr/subpage/?site=headquarter_new&mn=9291"
)

YEONGGWANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    YEONGGWANG_CANDIDATE_ID: {
        "decision": "retain_provider_and_retarget_home_to_course_catalogue",
        "provider": YEONGGWANG_PROVIDER,
        "url": YEONGGWANG_CANDIDATE_URL,
        "canonical_url": YEONGGWANG_CANONICAL_URL,
        "owner": YEONGGWANG_PROVIDER,
        "reason": "home page links to the structured institution course board",
    },
    YEONGGWANG_TOUR_CANDIDATE_ID: {
        "decision": "exclude_tourist_attraction_page",
        "provider": "",
        "url": YEONGGWANG_TOUR_CANDIDATE_URL,
        "canonical_url": "",
        "owner": "",
        "reason": "mn=7379 is Baeksu Coastal Road, not an education catalogue",
    },
}

YEONGGWANG_CATEGORY_PARTITION_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "source_total": 263,
    "filters": {
        "여성문화센터": 88,
        "장난감도서관": 101,
        "농업기술센터": 6,
        "청소년문화센터": 2,
        "기타": 22,
        "주민자치센터": 18,
        "노인복지관": 10,
        "군립도서관": 4,
        "보건소": 6,
        "신활력플러스": 4,
    },
    "filtered_union_count": 261,
    "pairwise_filter_overlap_count": 0,
    "unclassified": {
        "1078062": "23년 영광실내수영장 1월 강습반(초급) 모집",
        "1007328": "2019년 11월 영광실내수영장 수영 강습",
    },
    "current_category": "기타",
    "current_exact_institution": YEONGGWANG_CURRENT_BRANCH,
}

YEONGGWANG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    YEONGGWANG_PROVIDER: {
        "decision": "promote_exact_municipal_aggregate_catalogue",
        "exact_catalogue": YEONGGWANG_CATALOGUE_NAME,
        "canonical_url": YEONGGWANG_CANONICAL_URL,
        "current_exact_branch": YEONGGWANG_CURRENT_BRANCH,
        "audited_rows": 263,
        "audited_current_rows": 3,
    },
    YEONGGWANG_COUNTY_LIBRARY_PROVIDER: {
        "decision": "keep_separate_county_library_course_owner",
        "exact_branch": YEONGGWANG_COUNTY_LIBRARY_BRANCH,
        "canonical_url": YEONGGWANG_COUNTY_LIBRARY_URL,
        "audited_rows": 71,
        "audited_current_rows": 4,
        "current_title_overlap_with_municipal": 0,
        "municipal_historical_category_rows": 4,
    },
    YEONGGWANG_EDUCATION_LIBRARY_PROVIDER: {
        "decision": "keep_separate_office_of_education_library_owner",
        "exact_branch": YEONGGWANG_EDUCATION_LIBRARY_BRANCH,
        "catalogues": (
            YEONGGWANG_EDUCATION_LIBRARY_RESIDENT_URL,
            YEONGGWANG_EDUCATION_LIBRARY_STUDENT_URL,
        ),
        "audited_rows": 13,
        "audited_current_rows": 6,
        "current_title_overlap_with_municipal": 0,
    },
    YEONGGWANG_ARTS_PROVIDER: {
        "decision": "keep_separate_arts_centre_owner",
        "exact_branch": YEONGGWANG_ARTS_BRANCH,
    },
    YEONGGWANG_SWIMMING_PROVIDER: {
        "decision": "keep_separate_correct_jeonnam_swimming_owner",
        "exact_branch": YEONGGWANG_SWIMMING_BRANCH,
        "municipal_unclassified_historical_rows": 2,
        "stale_duplicate_provider": YEONGGWANG_SWIMMING_STALE_DUPLICATE_PROVIDER,
    },
}

YEONGGWANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "coverage_candidate_count": 4,
    "coverage_eligible_candidate_count": 2,
    "coverage_excluded_candidate_count": 2,
    "coverage_owner_arrays_stale_empty": True,
    "canonical_source_total": 263,
    "data_pages": 18,
    "page_counts": [15] * 17 + [8],
    "required_list_requests": 19,
    "source_identity_duplicates": 0,
    "source_status_counts": {"교육종료": 260, "신청하기": 3},
    "source_application_link_count": 3,
    "current_or_future_rows": 3,
    "expired_rows": 260,
    "current_exact_branch": YEONGGWANG_CURRENT_BRANCH,
    "current_exact_venue": "영광청년육아나눔터 2층 커뮤니티홀",
    "historical_education_period_formats": {
        "iso_two_dates": 260,
        "compact_two_dates": 2,
        "annual": 1,
    },
    "historical_application_period_formats": {
        "iso_two_dates": 256,
        "compact_two_dates": 2,
        "annual": 2,
        "empty": 3,
    },
    "tour_candidate_actual_page": "백수해안도로(1경)",
    "conclusion": (
        "retarget the county-home provider to the exact course board, walk all "
        "declared pages, and retain external facilities as separate owners"
    ),
}

YEONGGWANG_PII_FIELDS_DISCARDED = (
    "문의전화",
    "문의이메일",
    "첨부 및 다운로드 URL",
    "강좌신청정보",
    "나의 신청현황",
    "자유서술 내용",
    "CSRF token",
    "로그인 정보",
    "신청 form payload",
    "source HTML",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_INTEGER_RE = re.compile(r"\d{1,7}")
_ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")
_DATE_TIME_RE = re.compile(
    r"(?<!\d)(20\d{2}-\d{2}-\d{2})\s+"
    r"([01]\d|2[0-3]):([0-5]\d)(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*"
    r"\d{4}(?![A-Za-z0-9])"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CSRF_RE = re.compile(r"[0-9a-f]{64}")

_DOCUMENT_TITLE = "기관별 평생학습>평생학습>인구교육복지>영광군청"
_LIST_CAPTION = (
    "리스트 : 기관별 평생학습 게시판의 교육, 상태, 대상, 신청/정원(명), "
    "교육 기간, 신청 기간 리스트 입니다."
)
_LIST_HEADERS = (
    "대상",
    "교육",
    "신청/정원 (대기인원)",
    "교육 기간",
    "신청 기간",
    "상태",
)
_DETAIL_CAPTION = (
    "내용 : 기관별 평생학습 교육기관, 교육장소, 문의전화, 문의이메일, 상태, "
    "교육대상, 교육기간, 교육시간, 신청기간, 수강인원, 온라인 신청인원, "
    "대기신청인원, 수강료, 비고, 등록일, 강좌명, 강좌신청정보, 첨부파일, "
    "내용 등의 내용 페이지입니다."
)
_DETAIL_REQUIRED_FIELDS = (
    "교육기관",
    "교육장소",
    "문의전화",
    "문의이메일",
    "상태",
    "교육대상",
    "교육 기간",
    "교육 시간",
    "신청 기간",
    "수강인원",
    "온라인 신청인원",
    "대기신청인원",
    "수강료",
    "비고",
    "등록일",
    "강좌명",
    "강좌신청정보",
)
_DETAIL_ALLOWED_FIELDS = frozenset((*_DETAIL_REQUIRED_FIELDS, "첨부"))
_SEARCH_OPTIONS = (
    ("subject", "교육"),
    ("content", "내용"),
    ("writer_name", "담당부서"),
)
_CATEGORY_LINKS = (
    ("전체", YEONGGWANG_CANONICAL_URL, "municipal_all"),
    (
        "여성문화센터",
        YEONGGWANG_CANONICAL_URL + "&sc_cate=" + "%EC%97%AC%EC%84%B1%EB%AC%B8%ED%99%94%EC%84%BC%ED%84%B0",
        "municipal_filter",
    ),
    ("노인복지관", YEONGGWANG_SENIOR_URL, "separate_surface"),
    ("군립도서관", YEONGGWANG_COUNTY_LIBRARY_URL, "separate_owner"),
    (
        "장난감도서관",
        YEONGGWANG_CANONICAL_URL + "&sc_cate=" + "%EC%9E%A5%EB%82%9C%EA%B0%90%EB%8F%84%EC%84%9C%EA%B4%80",
        "municipal_filter",
    ),
    (
        "농업기술센터",
        YEONGGWANG_CANONICAL_URL + "&sc_cate=" + "%EB%86%8D%EC%97%85%EA%B8%B0%EC%88%A0%EC%84%BC%ED%84%B0",
        "municipal_filter",
    ),
    (
        "청소년문화센터",
        YEONGGWANG_CANONICAL_URL + "&sc_cate=" + "%EC%B2%AD%EC%86%8C%EB%85%84%EB%AC%B8%ED%99%94%EC%84%BC%ED%84%B0",
        "municipal_filter",
    ),
    ("신활력플러스", YEONGGWANG_SINHWALLYEOK_URL, "separate_owner"),
    (
        "기타",
        YEONGGWANG_CANONICAL_URL + "&sc_cate=" + "%EA%B8%B0%ED%83%80",
        "municipal_filter",
    ),
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "source_identity",
        "source_page",
        "source_state",
        "detail_state",
        "source_application_start",
        "source_application_stop",
        "source_total_capacity",
        "source_online_capacity",
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
        "문의전화",
        "문의이메일",
        "첨부",
        "강좌신청정보",
        "phone",
        "email",
        "attachment",
        "download_url",
        "content",
        "csrf_token_name",
        "form_payload",
        "source_html",
        "detail_pairs",
    }
)


class YeonggwangContractError(ValueError):
    """Raised when the audited Yeonggwang catalogue contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if node is None:
        return ""
    return _clean(node.get_text(" ", strip=True))


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _query_map(value: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(value)
    query: dict[str, str] = {}
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if key in query:
            raise YeonggwangContractError("duplicate URL query parameter")
        query[key] = item
    return parsed, query


def _safe_url_parts(value: str, host: str, path: str) -> tuple[Any, dict[str, str]]:
    try:
        parsed, query = _query_map(value)
        port = parsed.port
    except (ValueError, YeonggwangContractError) as exc:
        raise YeonggwangContractError("malformed URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != path
        or parsed.params
        or parsed.fragment
    ):
        raise YeonggwangContractError("URL escaped audited origin")
    return parsed, query


def _canonical_target_url(value: Any) -> str:
    try:
        _parsed, query = _safe_url_parts(
            _clean(value), YEONGGWANG_HOST, YEONGGWANG_PATH
        )
    except YeonggwangContractError:
        return ""
    if query != {
        "b_id": YEONGGWANG_BOARD,
        "site": YEONGGWANG_SITE,
        "mn": YEONGGWANG_MENU,
    }:
        return ""
    return YEONGGWANG_CANONICAL_URL


def is_yeonggwang_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == YEONGGWANG_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
        == YEONGGWANG_CANONICAL_URL
    )


is_target = is_yeonggwang_target


def yeonggwang_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query: list[tuple[str, str]] = [
        ("b_id", YEONGGWANG_BOARD),
        ("site", YEONGGWANG_SITE),
        ("mn", YEONGGWANG_MENU),
    ]
    if page > 1:
        query.append(("offset", str((page - 1) * YEONGGWANG_PAGE_SIZE)))
    return f"https://{YEONGGWANG_HOST}{YEONGGWANG_PATH}?{urlencode(query)}"


def yeonggwang_detail_url(identity: str) -> str:
    identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("identity must be a positive integer")
    return f"https://{YEONGGWANG_HOST}{YEONGGWANG_PATH}?" + urlencode(
        (
            ("b_id", YEONGGWANG_BOARD),
            ("site", YEONGGWANG_SITE),
            ("mn", YEONGGWANG_MENU),
            ("type", "view"),
            ("bs_idx", identity),
        )
    )


def yeonggwang_application_url(identity: str) -> str:
    identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("identity must be a positive integer")
    return f"https://{YEONGGWANG_HOST}{YEONGGWANG_PATH}?" + urlencode(
        (
            ("b_id", YEONGGWANG_BOARD),
            ("site", YEONGGWANG_SITE),
            ("mn", YEONGGWANG_MENU),
            ("type", "application"),
            ("bs_idx", identity),
        )
    )


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": YEONGGWANG_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise YeonggwangContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise YeonggwangContractError("HTTP redirect is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise YeonggwangContractError("empty HTTP response")
    if len(content) > YEONGGWANG_MAX_HTML_BYTES:
        raise YeonggwangContractError("HTTP response exceeded HTML byte cap")
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
        raise YeonggwangContractError("empty HTML")
    if len(content) > YEONGGWANG_MAX_HTML_BYTES:
        raise YeonggwangContractError("HTML exceeded byte cap")
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
        self.session = session_factory()
        self.requests = 0
        self.sessions_created = 1

    def get(self, url: str) -> BeautifulSoup:
        last_error: Optional[Exception] = None
        for _attempt in range(YEONGGWANG_FETCH_ATTEMPTS):
            try:
                self.requests += 1
                return _coerce_soup(self.fetcher(self.session, url, self.timeout))
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        _close_quietly(self.session)


def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise YeonggwangContractError(f"{label} changed")
    return nodes[0]


def _validate_category_links(soup: BeautifulSoup, page: int) -> None:
    nodes = soup.select("#board_category2 > ul.title_box > li > h4 > a[href]")
    actual: list[tuple[str, str]] = []
    for node in nodes:
        label = _text(node)
        absolute = urljoin(YEONGGWANG_CANONICAL_URL, _clean(node.get("href")))
        actual.append((label, absolute))
    expected = [(label, url) for label, url, _boundary in _CATEGORY_LINKS]
    if actual != expected:
        raise YeonggwangContractError(f"page {page} institution-owner tabs changed")
    active = [node for node in nodes if "on" in (node.get("class") or [])]
    if len(active) != 1 or _text(active[0]) != "전체":
        raise YeonggwangContractError(f"page {page} all-institution scope changed")


def _validate_search_form(soup: BeautifulSoup, page: int) -> None:
    form = _one(soup.select("form#frm"), f"page {page} catalogue search form")
    if _clean(form.get("method")).lower() != "get":
        raise YeonggwangContractError(f"page {page} search method changed")
    action = urljoin(YEONGGWANG_CANONICAL_URL, _clean(form.get("action")))
    parsed, query = _safe_url_parts(action, YEONGGWANG_HOST, YEONGGWANG_PATH)
    if query or parsed.query:
        raise YeonggwangContractError(f"page {page} search action changed")
    hidden_nodes = form.select("input[type='hidden'][name]")
    hidden_pairs = [
        (_clean(node.get("name")), _clean(node.get("value")))
        for node in hidden_nodes
    ]
    if len({name for name, _value in hidden_pairs}) != len(hidden_pairs):
        raise YeonggwangContractError(f"page {page} duplicate hidden search field")
    hidden = dict(hidden_pairs)
    token = hidden.pop("csrf_token_name", "")
    if not _CSRF_RE.fullmatch(token):
        raise YeonggwangContractError(f"page {page} CSRF token shape changed")
    if hidden != {
        "b_id": YEONGGWANG_BOARD,
        "site": YEONGGWANG_SITE,
        "mn": YEONGGWANG_MENU,
        "type": "lists",
        "sc_cate": "",
        "per_page": str(YEONGGWANG_PAGE_SIZE),
    }:
        raise YeonggwangContractError(f"page {page} search scope changed")
    options = tuple(
        (_clean(node.get("value")), _text(node))
        for node in form.select("select[name='sc_key'] > option")
    )
    if options != _SEARCH_OPTIONS:
        raise YeonggwangContractError(f"page {page} search options changed")
    keyword = _one(
        form.select("input[name='sc_word']"), f"page {page} search keyword"
    )
    if _clean(keyword.get("value")):
        raise YeonggwangContractError(f"page {page} keyword filter is active")


def _validate_course_href(
    value: Any,
    *,
    expected_type: str,
    page: int,
    identity: Optional[str] = None,
) -> tuple[str, str]:
    absolute = urljoin(YEONGGWANG_CANONICAL_URL, _clean(value))
    _parsed, query = _safe_url_parts(absolute, YEONGGWANG_HOST, YEONGGWANG_PATH)
    expected_keys = {"b_id", "site", "mn", "type", "bs_idx"}
    if "offset" in query:
        expected_keys.add("offset")
        expected_offset = str((page - 1) * YEONGGWANG_PAGE_SIZE)
        if page == 1 or query.get("offset") != expected_offset:
            raise YeonggwangContractError("course URL offset changed")
    if (
        set(query) != expected_keys
        or query.get("b_id") != YEONGGWANG_BOARD
        or query.get("site") != YEONGGWANG_SITE
        or query.get("mn") != YEONGGWANG_MENU
        or query.get("type") != expected_type
        or not _IDENTITY_RE.fullmatch(query.get("bs_idx", ""))
    ):
        raise YeonggwangContractError("course URL escaped catalogue")
    found = query["bs_idx"]
    if identity is not None and found != identity:
        raise YeonggwangContractError("course action identity changed")
    normalized = (
        yeonggwang_detail_url(found)
        if expected_type == "view"
        else yeonggwang_application_url(found)
    )
    return found, normalized


def _parse_source_state(cell: Any, *, page: int) -> tuple[str, str, str]:
    span = _one(cell.select(":scope > span"), f"page {page} source state")
    classes = set(span.get("class") or [])
    label = _text(span)
    links = span.select(":scope > a[href]")
    if classes == {"state_finish"} and label == "교육종료" and not links:
        return "CLOSED", "", label
    if classes == {"state_end"} and label == "접수종료" and not links:
        return "CLOSED", "", label
    if classes == {"state_G"} and label == "신청하기" and len(links) == 1:
        link = links[0]
        if _clean(link.get("title")) != "교육신청하기":
            raise YeonggwangContractError(f"page {page} application title changed")
        return "OPEN", _clean(link.get("href")), label
    raise YeonggwangContractError(f"page {page} unknown state/control contract")


def _dates(value: Any, label: str) -> tuple[str, str, str]:
    cleaned = _clean(value)
    iso = _ISO_DATE_RE.findall(cleaned)
    kind = "iso"
    if len(iso) == 2:
        raw = iso
    else:
        compact = _COMPACT_DATE_RE.findall(cleaned)
        if len(compact) == 2:
            raw = [f"{item[:4]}-{item[4:6]}-{item[6:]}" for item in compact]
            kind = "compact"
        elif cleaned.replace(" ", "") == "연중~연중":
            return "annual", "", ""
        else:
            raise YeonggwangContractError(f"{label} date range changed")
    try:
        start = date.fromisoformat(raw[0])
        end = date.fromisoformat(raw[1])
    except ValueError as exc:
        raise YeonggwangContractError(f"{label} date is invalid") from exc
    if end < start:
        raise YeonggwangContractError(f"{label} date range is reversed")
    return kind, start.isoformat(), end.isoformat()


def _education_period(value: Any, identity: str) -> tuple[str, str, str, str]:
    cleaned = _clean(value)
    kind, start, end = _dates(cleaned, f"course {identity} operating")
    if kind == "annual":
        return kind, start, end, ""
    pattern = _ISO_DATE_RE if kind == "iso" else _COMPACT_DATE_RE
    matches = list(pattern.finditer(cleaned))
    schedule = _clean(cleaned[matches[1].end() :]).lstrip("~ ")
    return kind, start, end, schedule


def _application_period(
    value: Any, identity: str
) -> tuple[str, str, str, str, str]:
    cleaned = _clean(value)
    if not cleaned:
        return "empty", "", "", "", ""
    timestamps = list(_DATE_TIME_RE.finditer(cleaned))
    if len(timestamps) == 2:
        values: list[str] = []
        parsed: list[datetime] = []
        for match in timestamps:
            item = f"{match.group(1)} {match.group(2)}:{match.group(3)}"
            try:
                parsed.append(datetime.strptime(item, "%Y-%m-%d %H:%M"))
            except ValueError as exc:
                raise YeonggwangContractError(
                    f"course {identity} application timestamp invalid"
                ) from exc
            values.append(item)
        if parsed[1] < parsed[0]:
            raise YeonggwangContractError(
                f"course {identity} application window is reversed"
            )
        return (
            "iso_datetime",
            values[0],
            values[1],
            parsed[0].date().isoformat(),
            parsed[1].date().isoformat(),
        )
    kind, start, end = _dates(cleaned, f"course {identity} application")
    return kind, start, end, start, end


def _parse_current_capacity(value: Any, identity: str) -> dict[str, int]:
    cleaned = _clean(value)
    match = re.fullmatch(
        r"수강인원\s*:\s*(\d{1,7})명\s+(\d{1,7})\s*/\s*(\d{1,7})"
        r"(?:\s*\(\s*(\d{1,7})\s*/\s*(\d{1,7})\s*\))?",
        cleaned,
    )
    if not match:
        raise YeonggwangContractError(f"course {identity} current capacity changed")
    total, current, online, wait_current, wait_total = (
        int(value) if value is not None else 0 for value in match.groups()
    )
    return {
        "overall_total": total,
        "capacity_current": current,
        "online_total": online,
        "wait_current": wait_current,
        "wait_total": wait_total,
    }


def _list_total(soup: BeautifulSoup, page: int) -> tuple[int, int]:
    node = _one(soup.select("#list_total_count"), f"page {page} total counter")
    strong = node.select(":scope > strong")
    if len(strong) != 2 or any(not _INTEGER_RE.fullmatch(_text(x)) for x in strong):
        raise YeonggwangContractError(f"page {page} total counter changed")
    total, current_page = int(_text(strong[0])), int(_text(strong[1]))
    expected_pages = max(1, (total + YEONGGWANG_PAGE_SIZE - 1) // YEONGGWANG_PAGE_SIZE)
    compact = re.sub(r"\s+", "", _text(node))
    expected = f"전체:{total},페이지:{current_page}/{expected_pages}"
    if compact != expected or current_page != page:
        raise YeonggwangContractError(f"page {page} declared pagination changed")
    return total, expected_pages


def _validate_pager(soup: BeautifulSoup, page: int, total_pages: int) -> None:
    pager = _one(soup.select("#paginate_complex"), f"page {page} pager")
    active = _one(pager.select("span.on"), f"page {page} active pager")
    if _text(active) != str(page):
        raise YeonggwangContractError(f"page {page} active pager changed")
    for link in pager.select("a[href]"):
        absolute = urljoin(YEONGGWANG_CANONICAL_URL, _clean(link.get("href")))
        _parsed, query = _safe_url_parts(
            absolute, YEONGGWANG_HOST, YEONGGWANG_PATH
        )
        if (
            set(query) != {"b_id", "site", "mn", "offset"}
            or query.get("b_id") != YEONGGWANG_BOARD
            or query.get("site") != YEONGGWANG_SITE
            or query.get("mn") != YEONGGWANG_MENU
        ):
            raise YeonggwangContractError(f"page {page} pager escaped catalogue")
        offset = query.get("offset", "")
        if offset:
            if not _INTEGER_RE.fullmatch(offset) or int(offset) % YEONGGWANG_PAGE_SIZE:
                raise YeonggwangContractError(f"page {page} pager offset changed")
            target = int(offset) // YEONGGWANG_PAGE_SIZE + 1
        else:
            target = 1
        if not 1 <= target <= total_pages:
            raise YeonggwangContractError(f"page {page} pager exceeds declared total")


def _validate_list_contract(
    soup: BeautifulSoup, page: int
) -> tuple[Any, int, int]:
    title = _one(soup.select("head > title"), f"page {page} document title")
    if _text(title) != _DOCUMENT_TITLE:
        raise YeonggwangContractError(f"page {page} exact site title changed")
    _validate_category_links(soup, page)
    _validate_search_form(soup, page)
    total, total_pages = _list_total(soup, page)
    table = _one(soup.select("#board_list > table"), f"page {page} course table")
    caption = _one(table.select(":scope > caption"), f"page {page} table caption")
    if _text(caption) != _LIST_CAPTION:
        raise YeonggwangContractError(f"page {page} table caption changed")
    headers = tuple(_text(node) for node in table.select(":scope > thead > tr > th"))
    if tuple(_normalized(item) for item in headers) != tuple(
        _normalized(item) for item in _LIST_HEADERS
    ):
        raise YeonggwangContractError(f"page {page} table headers changed")
    _validate_pager(soup, page, total_pages)
    return table, total, total_pages


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
    cutoff: date,
) -> tuple[list[dict[str, Any]], int, int, Counter[int]]:
    table, total, total_pages = _validate_list_contract(soup, page)
    rows: list[dict[str, Any]] = []
    capacity_shapes: Counter[int] = Counter()
    expected_cell_classes = (
        {"name"},
        {"subject"},
        {"number"},
        {"date", "date_time", "date_start"},
        {"date", "date_register"},
        {"state"},
    )
    for tr in table.select(":scope > tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 6:
            raise YeonggwangContractError(f"page {page} course row cell count changed")
        if tuple(set(cell.get("class") or []) for cell in cells) != expected_cell_classes:
            raise YeonggwangContractError(f"page {page} course cell classes changed")
        detail_link = _one(
            cells[1].select(":scope a[href]"), f"page {page} course detail link"
        )
        identity, raw_url = _validate_course_href(
            detail_link.get("href"), expected_type="view", page=page
        )
        title = _text(detail_link)
        if not title or _normalized(cells[1].get_text(" ", strip=True)) != _normalized(
            title
        ):
            raise YeonggwangContractError(f"course {identity} title changed")
        target = _text(cells[0])
        if not target:
            raise YeonggwangContractError(f"course {identity} target is empty")
        status, application_href, source_state = _parse_source_state(
            cells[5], page=page
        )
        period_kind, start, end, schedule = _education_period(
            _text(cells[3]), identity
        )
        (
            application_kind,
            application_start,
            application_stop,
            application_start_day,
            application_stop_day,
        ) = _application_period(_text(cells[4]), identity)
        current = bool(end and date.fromisoformat(end) >= cutoff)
        if period_kind == "annual":
            if status != "CLOSED":
                raise YeonggwangContractError(
                    f"course {identity} annual period has an active state"
                )
            current = False
        if period_kind == "compact" and status != "CLOSED":
            raise YeonggwangContractError(
                f"course {identity} compact historical date is active"
            )
        if not current and status == "OPEN":
            raise YeonggwangContractError(
                f"course {identity} expired row has an application action"
            )
        application_url = ""
        if status == "OPEN":
            if application_kind != "iso_datetime":
                raise YeonggwangContractError(
                    f"course {identity} active application window changed"
                )
            application_url = _validate_course_href(
                application_href,
                expected_type="application",
                page=page,
                identity=identity,
            )[1]
            if not (
                date.fromisoformat(application_start_day)
                <= cutoff
                <= date.fromisoformat(application_stop_day)
            ):
                raise YeonggwangContractError(
                    f"course {identity} active application window mismatch"
                )
        capacity_shapes[len(cells[2].select("span.applicant_number"))] += 1
        capacity = (
            _parse_current_capacity(_text(cells[2]), identity) if current else {}
        )
        rows.append(
            {
                "source_page": page,
                "identity": identity,
                "title": title,
                "target": target,
                "raw_period": _text(cells[3]),
                "period_kind": period_kind,
                "start_date": start,
                "end_date": end,
                "schedule": schedule,
                "application_kind": application_kind,
                "application_start": application_start,
                "application_stop": application_stop,
                "status": status,
                "source_state": source_state,
                "application_url": application_url,
                "raw_url": raw_url,
                "is_current": current,
                **capacity,
            }
        )
    return rows, total, total_pages, capacity_shapes


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    keys = (
        "identity",
        "title",
        "target",
        "raw_period",
        "period_kind",
        "start_date",
        "end_date",
        "schedule",
        "application_kind",
        "application_start",
        "application_stop",
        "status",
        "source_state",
        "application_url",
        "raw_url",
        "is_current",
        "overall_total",
        "capacity_current",
        "online_total",
        "wait_current",
        "wait_total",
    )
    return tuple(row.get(key) for key in keys)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


def _public_cell_text(cell: Any) -> str:
    clone = BeautifulSoup(str(cell), "lxml")
    for node in clone.select(".f_alert"):
        node.decompose()
    return _text(clone)


def _detail_pairs(table: Any, identity: str) -> tuple[dict[str, str], tuple[str, ...]]:
    pairs: dict[str, str] = {}
    order: list[str] = []
    content_rows = 0
    for tr in table.select(":scope > tbody > tr"):
        nodes = tr.find_all(["th", "td"], recursive=False)
        if len(nodes) == 1 and nodes[0].name == "td":
            if not nodes[0].select_one(":scope > div.board_view_contents"):
                raise YeonggwangContractError(
                    f"course {identity} unknown unlabelled detail row"
                )
            content_rows += 1
            continue
        if len(nodes) % 2 or any(
            node.name != ("th" if index % 2 == 0 else "td")
            for index, node in enumerate(nodes)
        ):
            raise YeonggwangContractError(f"course {identity} detail pairing changed")
        for index in range(0, len(nodes), 2):
            key = _text(nodes[index])
            value = _public_cell_text(nodes[index + 1])
            if not key:
                if value:
                    raise YeonggwangContractError(
                        f"course {identity} unlabelled detail value changed"
                    )
                continue
            if key in pairs:
                raise YeonggwangContractError(
                    f"course {identity} duplicate detail field"
                )
            order.append(key)
            pairs[key] = value
    if content_rows != 1:
        raise YeonggwangContractError(f"course {identity} detail content row changed")
    if set(pairs) != set(_DETAIL_REQUIRED_FIELDS) | (
        {"첨부"} if "첨부" in pairs else set()
    ):
        raise YeonggwangContractError(f"course {identity} detail fields changed")
    if not set(pairs) <= _DETAIL_ALLOWED_FIELDS:
        raise YeonggwangContractError(f"course {identity} unknown detail field")
    expected_order = list(_DETAIL_REQUIRED_FIELDS[:-1])
    if "첨부" in pairs:
        expected_order.append("첨부")
    expected_order.append("강좌신청정보")
    if order != expected_order:
        raise YeonggwangContractError(f"course {identity} detail field order changed")
    return pairs, tuple(order)


def _detail_integer(value: Any, identity: str, label: str, *, empty_ok: bool = False) -> int:
    cleaned = _clean(value)
    if empty_ok and not cleaned:
        return 0
    if not _INTEGER_RE.fullmatch(cleaned):
        raise YeonggwangContractError(f"course {identity} detail {label} changed")
    return int(cleaned)


def _validate_my_application(value: Any, identity: str) -> None:
    soup = BeautifulSoup(str(value), "lxml")
    text = _normalized(soup.get_text(" ", strip=True))
    if "로그인본인인증후신청정보를확인할수있습니다" not in text:
        raise YeonggwangContractError(
            f"course {identity} application-information notice changed"
        )
    link = _one(
        soup.select("a[href]"), f"course {identity} my-application link"
    )
    absolute = urljoin(YEONGGWANG_CANONICAL_URL, _clean(link.get("href")))
    _parsed, query = _safe_url_parts(
        absolute, YEONGGWANG_HOST, YEONGGWANG_PATH
    )
    if query != {
        "b_id": YEONGGWANG_BOARD,
        "site": YEONGGWANG_SITE,
        "mn": YEONGGWANG_MENU,
        "type": "my_application_list",
    }:
        raise YeonggwangContractError(
            f"course {identity} my-application owner changed"
        )


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
        raise YeonggwangContractError("persisted raw-field allowlist changed")
    lowered = {
        _clean(value).casefold()
        for value in _walk_values(row)
        if isinstance(value, str)
    }
    if lowered & {value.casefold() for value in _FORBIDDEN_PERSISTED_KEYS}:
        raise YeonggwangContractError("forbidden private/detail field reached output")
    for value in _walk_values(row):
        if isinstance(value, str) and _contains_pii(value):
            raise YeonggwangContractError("phone/email reached persisted allowlist")
    if row.get("description") != row.get("title"):
        raise YeonggwangContractError("description must contain title only")
    if bool(row.get("application_url")) != bool(row.get("reservation_available")):
        raise YeonggwangContractError("application availability is inconsistent")


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()[:12].upper()
    return f"{YEONGGWANG_PROVIDER}:INSTITUTION:{digest}"[:100]


def _parse_detail(
    parent: Mapping[str, Any], soup: BeautifulSoup, target: Any
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    title = _one(soup.select("head > title"), f"course {identity} detail title")
    if _text(title) != _DOCUMENT_TITLE:
        raise YeonggwangContractError(f"course {identity} detail owner changed")
    wrapper = _one(soup.select("#board_view"), f"course {identity} detail wrapper")
    table = _one(wrapper.select(":scope > table"), f"course {identity} detail table")
    caption = _one(table.select(":scope > caption"), f"course {identity} detail caption")
    if _text(caption) != _DETAIL_CAPTION:
        raise YeonggwangContractError(f"course {identity} detail caption changed")
    pairs, _order = _detail_pairs(table, identity)
    if _normalized(pairs["강좌명"]) != _normalized(parent.get("title")):
        raise YeonggwangContractError(f"course {identity} detail/list title mismatch")
    if _normalized(pairs["교육대상"]) != _normalized(parent.get("target")):
        raise YeonggwangContractError(f"course {identity} detail/list target mismatch")
    kind, start, end = _dates(pairs["교육 기간"], f"course {identity} detail operating")
    if kind != "iso" or (start, end) != (
        parent.get("start_date"),
        parent.get("end_date"),
    ):
        raise YeonggwangContractError(f"course {identity} detail/list period mismatch")
    if _normalized(pairs["교육 시간"]) != _normalized(parent.get("schedule")):
        raise YeonggwangContractError(f"course {identity} detail/list schedule mismatch")
    (
        apply_kind,
        apply_start,
        apply_stop,
        apply_start_day,
        apply_stop_day,
    ) = _application_period(pairs["신청 기간"], identity)
    if (
        apply_kind != parent.get("application_kind")
        or apply_start != parent.get("application_start")
        or apply_stop != parent.get("application_stop")
    ):
        raise YeonggwangContractError(
            f"course {identity} detail/list application window mismatch"
        )
    overall_total = _detail_integer(pairs["수강인원"], identity, "capacity")
    online_total = _detail_integer(
        pairs["온라인 신청인원"], identity, "online capacity"
    )
    wait_total = _detail_integer(
        pairs["대기신청인원"], identity, "wait capacity", empty_ok=True
    )
    if (
        overall_total != parent.get("overall_total")
        or online_total != parent.get("online_total")
        or wait_total != parent.get("wait_total")
    ):
        raise YeonggwangContractError(f"course {identity} detail/list capacity mismatch")
    status = _clean(parent.get("status"))
    detail_state = _clean(pairs["상태"])
    allowed_states = (
        {"접수대기", "접수중"}
        if status == "OPEN"
        else {"교육종료", "접수마감", "접수완료", "접수종료"}
    )
    if detail_state not in allowed_states:
        raise YeonggwangContractError(f"course {identity} detail/list state mismatch")
    branch = _clean(pairs["교육기관"])
    venue = _clean(pairs["교육장소"])
    if not branch or not venue:
        raise YeonggwangContractError(
            f"course {identity} exact institution/location is empty"
        )
    if _contains_pii(branch) or _contains_pii(venue):
        raise YeonggwangContractError(
            f"course {identity} institution/location contains phone or email"
        )
    application_info_cell = next(
        (
            td
            for th in table.select(":scope > tbody > tr > th")
            if _text(th) == "강좌신청정보"
            for td in [th.find_next_sibling("td")]
            if td is not None
        ),
        None,
    )
    if application_info_cell is None:
        raise YeonggwangContractError(
            f"course {identity} application-information cell changed"
        )
    _validate_my_application(application_info_cell, identity)

    open_now = status == "OPEN"
    capacity_current = int(parent.get("capacity_current") or 0)
    row = {
        "provider": YEONGGWANG_PROVIDER,
        "provider_course_id": f"{YEONGGWANG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": branch,
        "branch_code": _branch_code(branch),
        "provider_organizer": branch,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_stop}",
        "apply_start_date": apply_start_day,
        "apply_end_date": apply_stop_day,
        "status": status,
        "category": "교육",
        "program_type": "기관별 평생학습 강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_complete_declared_total_table+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "target": _clean(parent.get("target")),
        "schedule_raw": _clean(parent.get("schedule")),
        "room": venue,
        "venue": venue,
        "description": _clean(parent.get("title")),
        "capacity": online_total,
        "capacity_current": capacity_current,
        "capacity_total": online_total,
        "capacity_remaining": max(0, online_total - capacity_current),
        "waitlist_current": int(parent.get("wait_current") or 0),
        "waitlist_total": wait_total,
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": open_now,
        "application_url": (
            _clean(parent.get("application_url")) if open_now else ""
        ),
        "application_type": "ONLINE_RESERVATION" if open_now else "",
        "raw_url": _clean(parent.get("raw_url")),
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "source_identity": identity,
            "source_page": parent.get("source_page"),
            "source_state": parent.get("source_state"),
            "detail_state": detail_state,
            "source_application_start": parent.get("application_start"),
            "source_application_stop": parent.get("application_stop"),
            "source_total_capacity": overall_total,
            "source_online_capacity": online_total,
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "application_control_verified": True,
            "login_gate_verified": not open_now,
        },
    }
    _validate_persisted_row(row)
    return row


def _validate_login_gate(soup: BeautifulSoup, identity: str) -> None:
    title = _one(soup.select("head > title"), f"course {identity} login gate title")
    if _text(title) != "Message":
        raise YeonggwangContractError(f"course {identity} login gate title changed")
    if soup.select("form"):
        raise YeonggwangContractError(f"course {identity} login gate exposed a form")
    scripts = [
        node.get_text(" ", strip=True)
        for node in soup.select("script:not([src])")
        if "로그인 후 이용가능합니다." in node.get_text()
    ]
    if len(scripts) != 1:
        raise YeonggwangContractError(f"course {identity} login gate changed")
    normalized = re.sub(r"\s+", "", scripts[0])
    match = re.fullmatch(
        r"alert\('로그인후이용가능합니다[.]'\);location[.]href='([^']+)';",
        normalized,
    )
    if not match:
        raise YeonggwangContractError(f"course {identity} login gate script changed")
    destination = urljoin(YEONGGWANG_CANONICAL_URL, match.group(1))
    _parsed, query = _safe_url_parts(
        destination, YEONGGWANG_HOST, "/subpage/"
    )
    if set(query) != {"site", "mn", "ret_url"} or query.get("site") != (
        YEONGGWANG_SITE
    ) or query.get("mn") != "9641":
        raise YeonggwangContractError(
            f"course {identity} login gate destination changed"
        )
    return_url = urljoin(YEONGGWANG_CANONICAL_URL, query.get("ret_url", ""))
    _return_parsed, return_query = _safe_url_parts(
        return_url, YEONGGWANG_HOST, YEONGGWANG_PATH
    )
    if return_query != {
        "b_id": YEONGGWANG_BOARD,
        "site": YEONGGWANG_SITE,
        "mn": YEONGGWANG_MENU,
        "type": "application",
        "bs_idx": identity,
    }:
        raise YeonggwangContractError(
            f"course {identity} login return identity changed"
        )


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
        "source_count": 1,
        "source_total": 0,
        "source_rows": 0,
        "page_counts": {},
        "data_pages": 0,
        "declared_total_pages": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "list_rechecks": 0,
        "current_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "application_gate_attempts": 0,
        "application_gate_pages": 0,
        "source_status_counts": {},
        "source_application_link_count": 0,
        "historical_period_kind_counts": {},
        "historical_application_kind_counts": {},
        "capacity_shape_counts": {},
        "branch_counts": {},
        "duplicate_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def _failure(message: str, **updates: Any) -> dict[str, Any]:
    meta = _base_meta()
    meta.update(updates)
    meta["configured_collection_error"] = message
    return meta


def collect_yeonggwang_education_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 30,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the complete current/future Yeonggwang education snapshot."""

    if not is_yeonggwang_target(target):
        return [], YEONGGWANG_PARSER, _failure(
            "target does not match the canonical Yeonggwang education catalogue"
        )
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        timeout_value = int(timeout)
        cutoff = _today(today)
        if (
            isinstance(max_pages, bool)
            or isinstance(detail_limit, bool)
            or isinstance(timeout, bool)
            or allowed_pages < 2
            or allowed_details < 0
            or timeout_value <= 0
        ):
            raise ValueError
    except (TypeError, ValueError):
        return [], YEONGGWANG_PARSER, _failure(
            "max_pages/detail_limit/timeout/today are invalid"
        )
    try:
        client = _Client(
            timeout=timeout_value,
            fetcher=fetcher or _default_fetcher,
            session_factory=session_factory or _default_session_factory,
        )
    except Exception as exc:
        return [], YEONGGWANG_PARSER, _failure(
            f"client setup: {type(exc).__name__}: {exc}"
        )

    errors: list[str] = []
    source_cap_reached = False
    all_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    first_rows: list[dict[str, Any]] = []
    source_total = 0
    total_pages = 0
    list_requests = 0
    list_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    gate_attempts = 0
    gate_pages = 0
    capacity_shapes: Counter[int] = Counter()

    try:
        try:
            first_soup = client.get(YEONGGWANG_CANONICAL_URL)
            list_requests += 1
            first_rows, source_total, total_pages, shapes = _parse_list_page(
                first_soup, 1, cutoff
            )
            capacity_shapes.update(shapes)
            all_rows.extend(first_rows)
            page_counts[1] = len(first_rows)
        except Exception as exc:
            errors.append(f"page 1: {type(exc).__name__}: {exc}")

        required_list_requests = total_pages + 1 if total_pages else 0
        if not errors and required_list_requests > allowed_pages:
            source_cap_reached = True
            errors.append(
                f"max_pages cap allows {allowed_pages}; {required_list_requests} "
                "declared list requests including page-one recheck are required"
            )

        if not errors:
            for page in range(2, total_pages + 1):
                try:
                    soup = client.get(yeonggwang_list_url(page))
                    list_requests += 1
                    rows, declared_total, declared_pages, shapes = _parse_list_page(
                        soup, page, cutoff
                    )
                    if declared_total != source_total or declared_pages != total_pages:
                        raise YeonggwangContractError(
                            f"page {page} total changed during pagination"
                        )
                    capacity_shapes.update(shapes)
                    all_rows.extend(rows)
                    page_counts[page] = len(rows)
                except Exception as exc:
                    errors.append(f"page {page}: {type(exc).__name__}: {exc}")
                    break

        if not errors:
            try:
                recheck = client.get(YEONGGWANG_CANONICAL_URL)
                list_requests += 1
                rechecked_rows, re_total, re_pages, _shapes = _parse_list_page(
                    recheck, 1, cutoff
                )
                list_rechecks = 1
                if (
                    re_total != source_total
                    or re_pages != total_pages
                    or _page_signature(rechecked_rows) != _page_signature(first_rows)
                ):
                    raise YeonggwangContractError("page-one recheck changed")
            except Exception as exc:
                errors.append(f"page-one recheck: {type(exc).__name__}: {exc}")

        if source_total:
            expected_counts = {
                page: (
                    YEONGGWANG_PAGE_SIZE
                    if page < total_pages
                    else source_total - YEONGGWANG_PAGE_SIZE * (total_pages - 1)
                )
                for page in range(1, total_pages + 1)
            }
            if page_counts and page_counts != expected_counts:
                errors.append(
                    f"page row counts {page_counts} differ from declared total "
                    f"shape {expected_counts}"
                )
            if len(all_rows) != source_total:
                errors.append(
                    f"declared total {source_total} differs from parsed {len(all_rows)}"
                )

        identities = [_clean(row.get("identity")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate source identities")

        current_parents = [row for row in all_rows if row.get("is_current")]
        expired_count = len(all_rows) - len(current_parents)
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
                try:
                    detail = client.get(_clean(parent.get("raw_url")))
                    row = _parse_detail(parent, detail, target)
                    detail_pages += 1
                    if row.get("status") == "OPEN":
                        gate_attempts += 1
                        gate = client.get(_clean(row.get("application_url")))
                        _validate_login_gate(
                            gate, _clean(row["raw_fields"]["source_identity"])
                        )
                        gate_pages += 1
                        row["raw_fields"]["login_gate_verified"] = True
                        _validate_persisted_row(row)
                    detailed_rows.append(row)
                except Exception as exc:
                    errors.append(
                        f"course {parent.get('identity')} detail/control: "
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
                    errors.append(
                        f"persisted row validation: {type(exc).__name__}: {exc}"
                    )
                    result = []
        result.sort(
            key=lambda row: (
                _clean(row.get("start_date")),
                _clean(row.get("title")),
                _clean(row.get("provider_course_id")),
            )
        )

        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and total_pages > 0
            and len(page_counts) == total_pages
            and len(all_rows) == source_total
            and list_rechecks == 1
            and list_requests == required_list_requests
        )
        details_complete = bool(
            snapshot_complete
            and detail_attempts == len(current_parents)
            and detail_pages == len(current_parents)
        )
        open_current = sum(row.get("status") == "OPEN" for row in detailed_rows)
        controls_complete = bool(
            details_complete
            and gate_attempts == open_current
            and gate_pages == open_current
            and all(
                row.get("raw_fields", {}).get("application_control_verified")
                and row.get("raw_fields", {}).get("login_gate_verified")
                for row in detailed_rows
            )
        )
        full_snapshot_validated = bool(
            snapshot_complete
            and pagination_complete
            and details_complete
            and controls_complete
        )
        source_states = Counter(_clean(row.get("source_state")) for row in all_rows)
        period_kinds = Counter(_clean(row.get("period_kind")) for row in all_rows)
        application_kinds = Counter(
            _clean(row.get("application_kind")) for row in all_rows
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in detailed_rows)
        meta = _base_meta()
        meta.update(
            {
                "pages": client.requests,
                "request_count": client.requests,
                "sessions_created": client.sessions_created,
                "source_total": source_total,
                "source_rows": len(all_rows),
                "page_counts": page_counts,
                "data_pages": len(page_counts),
                "declared_total_pages": total_pages,
                "required_list_requests": required_list_requests,
                "list_requests": list_requests,
                "list_rechecks": list_rechecks,
                "current_count": len(current_parents),
                "expired_count": expired_count,
                "returned_count": len(result),
                "detail_attempts": detail_attempts,
                "detail_pages": detail_pages,
                "application_gate_attempts": gate_attempts,
                "application_gate_pages": gate_pages,
                "source_status_counts": dict(source_states),
                "source_application_link_count": sum(
                    bool(row.get("application_url")) for row in all_rows
                ),
                "historical_period_kind_counts": dict(period_kinds),
                "historical_application_kind_counts": dict(application_kinds),
                "capacity_shape_counts": dict(capacity_shapes),
                "branch_counts": dict(branch_counts),
                "duplicate_count": duplicate_count,
                "pagination_detected": total_pages > 1,
                "pagination_complete": pagination_complete,
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": full_snapshot_validated,
                "source_cap_reached": source_cap_reached,
                "no_current_data": bool(snapshot_complete and not current_parents),
                "no_current_reason": (
                    "the complete Yeonggwang education catalogue has no "
                    "current/future rows"
                    if snapshot_complete and not current_parents
                    else ""
                ),
                "configured_collection_error": "; ".join(errors),
            }
        )
        if errors:
            return [], YEONGGWANG_PARSER, meta
        return result, YEONGGWANG_PARSER, meta
    finally:
        client.close()


collect = collect_yeonggwang_education_courses


__all__ = [
    "YEONGGWANG_CANDIDATE_AUDIT",
    "YEONGGWANG_CANDIDATE_ID",
    "YEONGGWANG_CANDIDATE_URL",
    "YEONGGWANG_CANONICAL_URL",
    "YEONGGWANG_CATEGORY_PARTITION_AUDIT",
    "YEONGGWANG_COUNTY_LIBRARY_BRANCH",
    "YEONGGWANG_COUNTY_LIBRARY_PROVIDER",
    "YEONGGWANG_CURRENT_BRANCH",
    "YEONGGWANG_DISCOVERY_AUDIT",
    "YEONGGWANG_EDUCATION_LIBRARY_BRANCH",
    "YEONGGWANG_EDUCATION_LIBRARY_PROVIDER",
    "YEONGGWANG_HOST",
    "YEONGGWANG_MUNICIPALITY_CODE",
    "YEONGGWANG_MUNICIPALITY_NAME",
    "YEONGGWANG_OWNER_BOUNDARY_AUDIT",
    "YEONGGWANG_PARSER",
    "YEONGGWANG_PII_FIELDS_DISCARDED",
    "YEONGGWANG_PROVIDER",
    "YEONGGWANG_SWIMMING_PROVIDER",
    "YEONGGWANG_TOUR_CANDIDATE_ID",
    "YEONGGWANG_TOUR_CANDIDATE_URL",
    "YeonggwangContractError",
    "collect",
    "collect_yeonggwang_education_courses",
    "is_target",
    "is_yeonggwang_target",
    "yeonggwang_application_url",
    "yeonggwang_detail_url",
    "yeonggwang_list_url",
]
