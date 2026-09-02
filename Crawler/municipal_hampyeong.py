"""Fail-closed collector for Hampyeong County's lifelong-learning catalogue.

The integrated-reservation discovery candidate points at the county job
board, not at a course list.  Its provider identity was subsequently reused
for ``hplifeedu.com``, where the actual county-owned course catalogue lives.
This module retains that provider identity but accepts only the canonical
``pjEducate.php`` list endpoint.

The live catalogue has no declared total.  Completeness is therefore proved
by walking every ten-row page until the first explicit empty sentinel and
then re-fetching page one.  Current/future rows are verified against their
detail pages.  Active application links are course-bound, checked against the
official unauthenticated login gate, and never submitted.

Instructor names, inquiry contacts, attachments, related-site fields,
application forms and source HTML are deliberately discarded.  Pagination,
identity, schema, status, control, detail or PII drift invalidates the whole
snapshot.  The Office of Education's Hampyeong Library and the county public
library remain separate facility/course owners.
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


HAMPYEONG_PROVIDER = "MUNI_WWW_HAMPYEONG_GO_KR_922B63EF"
HAMPYEONG_CANDIDATE_ID = "MUNI_IR_739512DE876F"
HAMPYEONG_MUNICIPALITY_CODE = "1282000000"
HAMPYEONG_MUNICIPALITY_NAME = "전남광주통합특별시 함평군"
HAMPYEONG_BRANCH = "함평군 평생학습"
HAMPYEONG_HOST = "hplifeedu.com"
HAMPYEONG_LIST_PATH = "/pj/pjEducate.php"
HAMPYEONG_APPLY_PATH = "/pj/pjEducateApply.php"
HAMPYEONG_LOGIN_PATH = "/pj/pjLogin.php"
HAMPYEONG_PAGE_ID = "hplifeedu0201000000"
HAMPYEONG_LEGACY_PAGE_ID = "hplifeedu0200000000"
HAMPYEONG_PAGE_SIZE = 10
HAMPYEONG_FETCH_ATTEMPTS = 2
HAMPYEONG_MAX_HTML_BYTES = 3_000_000
HAMPYEONG_CANONICAL_URL = (
    f"https://{HAMPYEONG_HOST}{HAMPYEONG_LIST_PATH}?"
    f"action=list&pageID={HAMPYEONG_PAGE_ID}"
)
HAMPYEONG_EXISTING_HOME_URL = f"https://{HAMPYEONG_HOST}/"
HAMPYEONG_REGISTERED_CANDIDATE_URL = (
    "https://www.hampyeong.go.kr/boardList.do?boardId=TOTJOB&pageId=www679"
)
HAMPYEONG_CURRENT_NOTICE_URL = (
    "https://hplifeedu.com/bb/bbBoard.php?action=view&"
    "pageID=hplifeedu0401000000&boardID=NOTICE&SEQ=1966126"
)
HAMPYEONG_NOTICE_LIST_URL = (
    "https://hplifeedu.com/bb/bbBoard.php?"
    "boardID=NOTICE&pageID=hplifeedu0401000000"
)
HAMPYEONG_PERSONAL_ENROLMENT_URL = (
    "https://hplifeedu.com/pj/pjEducateMy.php?"
    "action=list&pageID=hplifeedu0202000000"
)
HAMPYEONG_PARSER = (
    "hampyeong_lifelong_complete_unknown_total_pages+explicit_empty_sentinel+"
    "stable_page1+current_detail+step_and_course_bound_application_controls+"
    "login_gate+pii_allowlist"
)

# Separate owners and excluded discovery surfaces.
HAMPYEONG_EDUCATION_LIBRARY_PROVIDER = "CULTURE_PUBLIC_LIBRARY_401DCDE775"
HAMPYEONG_EDUCATION_LIBRARY_BRANCH = (
    "전남광주통합특별시교육청함평도서관"
)
HAMPYEONG_EDUCATION_LIBRARY_URL = (
    "https://hplib.jne.go.kr/index.es?sid=b6"
)
HAMPYEONG_EDUCATION_LIBRARY_LECTURE_URL = (
    "https://hplib.jne.go.kr/lecture.es?mid=b60402000000"
)
HAMPYEONG_COUNTY_LIBRARY_PROVIDER = "CULTURE_PUBLIC_LIBRARY_6364783841"
HAMPYEONG_COUNTY_LIBRARY_BRANCH = "함평군립도서관"
HAMPYEONG_COUNTY_LIBRARY_URL = "http:" "//www.butterflyhp.or.kr"
HAMPYEONG_COUNTY_MUSEUM_PROVIDER = "CULTURE_ART_MUSEUM_2D164E6615"
HAMPYEONG_COUNTY_MUSEUM_BRANCH = "함평군립미술관"
HAMPYEONG_COUNTY_MUSEUM_URL = "https://www.hpart.or.kr"
HAMPYEONG_NAVER_CANDIDATE_ID = "MUNI_IR_98844C9A7FD0"
HAMPYEONG_NAVER_URL = "https://blog.naver.com/hpcounty/224262477225"
HAMPYEONG_DATA_CANDIDATE_ID = "MUNI_IR_87B09B227264"
HAMPYEONG_DATA_URL = "https://www.data.go.kr/data/15123875/fileData.do"

HAMPYEONG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, str]] = {
    HAMPYEONG_CANDIDATE_ID: {
        "decision": "retain_provider_but_replace_job_board_with_course_catalogue",
        "provider": HAMPYEONG_PROVIDER,
        "url": HAMPYEONG_REGISTERED_CANDIDATE_URL,
        "canonical_url": HAMPYEONG_CANONICAL_URL,
        "owner": HAMPYEONG_PROVIDER,
        "reason": "TOTJOB is the county job board; hplifeedu is the course owner",
    },
    HAMPYEONG_NAVER_CANDIDATE_ID: {
        "decision": "exclude_social_media_notice",
        "provider": "",
        "url": HAMPYEONG_NAVER_URL,
        "canonical_url": "",
        "owner": "",
        "reason": "social-media article is not a stable official catalogue",
    },
    HAMPYEONG_DATA_CANDIDATE_ID: {
        "decision": "exclude_static_open_data_file",
        "provider": "",
        "url": HAMPYEONG_DATA_URL,
        "canonical_url": "",
        "owner": "",
        "reason": "historical file dataset is not a live enrolment catalogue",
    },
}

HAMPYEONG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    HAMPYEONG_PROVIDER: {
        "decision": "replace_partial_generic_crawl_under_existing_owner",
        "exact_branch": HAMPYEONG_BRANCH,
        "catalogues": (HAMPYEONG_CANONICAL_URL,),
        "excluded_aliases": (
            HAMPYEONG_REGISTERED_CANDIDATE_URL,
            HAMPYEONG_EXISTING_HOME_URL,
            HAMPYEONG_NOTICE_LIST_URL,
            HAMPYEONG_PERSONAL_ENROLMENT_URL,
        ),
    },
    HAMPYEONG_EDUCATION_LIBRARY_PROVIDER: {
        "decision": "keep_separate_office_of_education_library_owner",
        "exact_branch": HAMPYEONG_EDUCATION_LIBRARY_BRANCH,
        "catalogues": (HAMPYEONG_EDUCATION_LIBRARY_LECTURE_URL,),
        "audited_rows": 8,
        "audited_current_rows": 0,
        "exact_title_overlap_with_municipal_catalogue": 0,
    },
    HAMPYEONG_COUNTY_LIBRARY_PROVIDER: {
        "decision": "keep_separate_county_library_facility_owner",
        "exact_branch": HAMPYEONG_COUNTY_LIBRARY_BRANCH,
        "catalogues": (HAMPYEONG_COUNTY_LIBRARY_URL,),
    },
    HAMPYEONG_COUNTY_MUSEUM_PROVIDER: {
        "decision": "keep_separate_museum_facility_owner",
        "exact_branch": HAMPYEONG_COUNTY_MUSEUM_BRANCH,
        "catalogues": (HAMPYEONG_COUNTY_MUSEUM_URL,),
    },
}

HAMPYEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "coverage_candidate_count": 3,
    "coverage_eligible_candidate_count": 1,
    "coverage_excluded_candidate_count": 2,
    "coverage_owner_arrays_stale_empty": True,
    "registered_candidate_is_job_board": True,
    "existing_provider": HAMPYEONG_PROVIDER,
    "existing_target_status": "partial",
    "existing_quality_collected": 10,
    "existing_quality_score": 53.8,
    "existing_quality_grade": "C",
    "existing_parser": "generic_card+generic_table",
    "existing_command_row_cap": 50,
    "existing_command_allows_partial_save": True,
    "canonical_source_total": 280,
    "data_pages": 28,
    "page_counts": [10] * 28,
    "sentinel_page": 29,
    "required_list_requests": 30,
    "source_identity_duplicates": 0,
    "source_identity_order_strictly_descending": True,
    "deleted_identity_gaps": 5,
    "current_or_future_rows": 0,
    "latest_source_end_date": "2026-03-11",
    "source_step_counts": {"1": 79, "2": 22, "3": 14, "4": 165},
    "source_status_counts": {"SCHEDULED": 79, "OPEN": 14, "CLOSED": 187},
    "source_course_bound_application_links": 14,
    "current_notice_is_not_structured_catalogue": HAMPYEONG_CURRENT_NOTICE_URL,
    "exact_current_branch": HAMPYEONG_BRANCH,
    "conclusion": (
        "retain the existing provider, retarget it to the exact course list, "
        "replace capped partial collection, and keep libraries/museum separate"
    ),
}

HAMPYEONG_PII_FIELDS_DISCARDED = (
    "강사명",
    "문의처",
    "관련 홈페이지",
    "첨부파일 및 다운로드 URL",
    "강사은행",
    "수강현황/신청자 정보",
    "로그인 정보",
    "신청 form payload",
    "source HTML",
)


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_ISO_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_TIMESTAMP_RE = re.compile(
    r"^(20\d{2}-\d{2}-\d{2})\s+([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$"
)
_KOREAN_DATE_RE = re.compile(
    r"(?<!\d)(?:(20\d{2})\s*[.]\s*)?(\d{1,2})\s*[.]\s*"
    r"(\d{1,2})\s*[.]?"
)
_INTEGER_RE = re.compile(r"^\d{1,7}$")
_DETAIL_INTEGER_RE = re.compile(r"^(\d{1,7})\s*명$")
_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*"
    r"\d{4}(?![A-Za-z0-9])"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LIST_HEADER_ROWS = (
    (
        "구분",
        "강좌명",
        "교육장소",
        "신청 인원",
        "모집 인원",
        "강사명",
        "강의일시",
        "신청하기",
    ),
    ("일시", "운영시간"),
)
_LIST_CAPTION = (
    "함평군 평생교육 강좌정보의 구분, 강좌명, 교육장소, 신청인원, 모집인원, "
    "강사명, 강의일시(요일,운영시간),상세보기를 안내하고 있습니다."
)
_CATEGORY_OPTIONS = (
    ("0", "전체보기"),
    ("1", "주민복지실"),
    ("2", "행정지원과"),
    ("3", "문화관광체육과"),
    ("4", "보건소"),
    ("5", "농업기술센터"),
    ("6", "함평읍"),
    ("7", "손불면"),
    ("8", "신광면"),
    ("9", "학교면"),
    ("10", "엄다면"),
    ("11", "대동면"),
    ("12", "나산면"),
    ("13", "해보면"),
    ("14", "월야면"),
    ("15", "총무과"),
    ("16", "평생학습관"),
    ("17", "마을평생학습센터"),
)
_DETAIL_FIELDS = (
    "강좌명",
    "구분",
    "교육장소",
    "신청인원",
    "모집인원",
    "강사명",
    "신청기간",
    "교육기간",
    "운영시간",
    "문의처",
    "관련 홈페이지",
    "첨부파일",
)
_DETAIL_CAPTION = (
    "강좌정보를 상세히 보여드립니다. 구분, 강좌명, 교육장소, 신청인원, "
    "모집인원, 강사명, 강의일시 등을 안내하고 있습니다"
)
_INERT_CONTROL_STATUS: Mapping[str, str] = {
    "alert('수강신청이 시작되지 않았습니다. (진행상태: 신청전)'); return false;": "SCHEDULED",
    "alert('수강신청 기간이 아닙니다.'); return false;": "SCHEDULED",
    "alert('수강신청 기간이 종료되었습니다.'); return false;": "CLOSED",
    "alert('수강신청이 종료된 강의입니다.'); return false;": "CLOSED",
}
_CHECK_DATE_REQUIRED = (
    'functioncheckClassDate(el){',
    'vartargetRow=el.closest("tr");',
    'varstep=targetRow.dataset.step;',
    'varstartStr=targetRow.dataset.start;',
    'varstopStr=targetRow.dataset.stop;',
    'if(step==="1")',
    'if(step==="4")',
    'if(step==="2"&&startStr&&stopStr)',
    'if(now<startDate)',
    'if(now>endDate)',
    'returntrue;',
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "source_identity",
        "source_page",
        "source_category",
        "source_step",
        "source_status",
        "source_application_start",
        "source_application_stop",
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
        "강사명",
        "문의처",
        "관련 홈페이지",
        "첨부파일",
        "instructor",
        "contact",
        "phone",
        "email",
        "attachment",
        "download_url",
        "detail_pairs",
        "form_payload",
        "source_html",
    }
)


class HampyeongContractError(ValueError):
    """Raised when an audited Hampyeong catalogue contract changes."""


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


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
            raise HampyeongContractError("duplicate URL query parameter")
        query[key] = item
    return parsed, query


def _canonical_target_url(value: Any) -> str:
    try:
        parsed, query = _query_map(_clean(value))
        port = parsed.port
    except (ValueError, HampyeongContractError):
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname != HAMPYEONG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != HAMPYEONG_LIST_PATH
        or parsed.params
        or parsed.fragment
        or query != {"action": "list", "pageID": HAMPYEONG_PAGE_ID}
    ):
        return ""
    return HAMPYEONG_CANONICAL_URL


def is_hampyeong_target(target: Any) -> bool:
    return (
        _clean(_target_value(target, "provider")) == HAMPYEONG_PROVIDER
        and _canonical_target_url(_target_value(target, "url"))
        == HAMPYEONG_CANONICAL_URL
    )


is_target = is_hampyeong_target


def hampyeong_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    query: list[tuple[str, str]] = []
    if page > 1:
        query.append(("movePage", str(page)))
    query.extend((("action", "list"), ("pageID", HAMPYEONG_PAGE_ID)))
    return f"https://{HAMPYEONG_HOST}{HAMPYEONG_LIST_PATH}?{urlencode(query)}"


def hampyeong_detail_url(identity: str) -> str:
    identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("identity must be a positive integer")
    return f"https://{HAMPYEONG_HOST}{HAMPYEONG_LIST_PATH}?" + urlencode(
        (("pageID", HAMPYEONG_PAGE_ID), ("action", "view"), ("seq", identity))
    )


def hampyeong_application_url(identity: str) -> str:
    identity = _clean(identity)
    if not _IDENTITY_RE.fullmatch(identity):
        raise ValueError("identity must be a positive integer")
    return f"https://{HAMPYEONG_HOST}{HAMPYEONG_APPLY_PATH}?" + urlencode(
        (("pageID", HAMPYEONG_PAGE_ID), ("action", "insert"), ("eseq", identity))
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
            "Referer": HAMPYEONG_CANONICAL_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise HampyeongContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise HampyeongContractError("HTTP redirect is not accepted")
    content = getattr(response, "content", b"")
    if not content:
        raise HampyeongContractError("empty HTTP response")
    if len(content) > HAMPYEONG_MAX_HTML_BYTES:
        raise HampyeongContractError("HTTP response exceeded HTML byte cap")
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
        raise HampyeongContractError("empty HTML")
    if len(content) > HAMPYEONG_MAX_HTML_BYTES:
        raise HampyeongContractError("HTML exceeded byte cap")
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
        for _attempt in range(HAMPYEONG_FETCH_ATTEMPTS):
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
        raise HampyeongContractError(f"{label} changed")
    return nodes[0]


def _validate_check_date_script(soup: BeautifulSoup, page: int) -> None:
    scripts = [
        node.get_text("\n", strip=True)
        for node in soup.select("script:not([src])")
        if "checkClassDate" in node.get_text()
    ]
    if len(scripts) != 1:
        raise HampyeongContractError(f"page {page} application-check script changed")
    normalized = re.sub(r"\s+", "", scripts[0])
    if any(snippet not in normalized for snippet in _CHECK_DATE_REQUIRED):
        raise HampyeongContractError(f"page {page} application-check logic changed")


def _validate_page_contract(soup: BeautifulSoup, page: int) -> Any:
    title = _one(soup.select("head > title"), f"page {page} document title")
    if _clean(title.get_text(" ", strip=True)) != "강좌정보 | 함평군 평생학습":
        raise HampyeongContractError(f"page {page} exact site title changed")
    sitemap = _one(
        soup.select("header.sitemap-title > h2"), f"page {page} site identity"
    )
    if _clean(sitemap.get_text(" ", strip=True)) != "함평군 평생학습 사이트맵":
        raise HampyeongContractError(f"page {page} exact branch name changed")

    form = _one(
        soup.select("form#search_form[name='search_form']"),
        f"page {page} search form",
    )
    if _clean(form.get("method")).lower() != "post":
        raise HampyeongContractError(f"page {page} search method changed")
    action = urljoin(HAMPYEONG_CANONICAL_URL, _clean(form.get("action")))
    try:
        parsed = urlparse(action)
        port = parsed.port
    except ValueError as exc:
        raise HampyeongContractError(
            f"page {page} search action changed"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != HAMPYEONG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != HAMPYEONG_LIST_PATH
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise HampyeongContractError(f"page {page} search action changed")
    hidden_nodes = form.select("input[type='hidden'][name]")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in hidden_nodes
    }
    if len(hidden) != len(hidden_nodes):
        raise HampyeongContractError(f"page {page} duplicate hidden search field")
    if hidden != {"pageID": HAMPYEONG_PAGE_ID, "action": "list"}:
        raise HampyeongContractError(f"page {page} search scope changed")
    options = tuple(
        (_clean(node.get("value")), _clean(node.get_text(" ", strip=True)))
        for node in form.select("select[name='category'] > option")
    )
    if options != _CATEGORY_OPTIONS:
        raise HampyeongContractError(f"page {page} all-category selector changed")
    selected = form.select("select[name='category'] > option[selected]")
    if selected and (
        len(selected) != 1 or _clean(selected[0].get("value")) != "0"
    ):
        raise HampyeongContractError(f"page {page} category filter is active")
    keyword = _one(
        form.select("input[name='searchQuery']"), f"page {page} search keyword"
    )
    if _clean(keyword.get("value")):
        raise HampyeongContractError(f"page {page} keyword filter is active")

    table = _one(soup.select("table.basic_table"), f"page {page} catalogue table")
    caption = _one(table.select(":scope > caption"), f"page {page} table caption")
    if _clean(caption.get_text(" ", strip=True)) != _LIST_CAPTION:
        raise HampyeongContractError(f"page {page} catalogue caption changed")
    header_rows = table.select(":scope > thead > tr")
    headers = tuple(
        tuple(
            _clean(node.get_text(" ", strip=True))
            for node in tr.find_all(["th", "td"], recursive=False)
        )
        for tr in header_rows
    )
    if headers != _LIST_HEADER_ROWS:
        raise HampyeongContractError(f"page {page} catalogue headers changed")
    if len(header_rows) != 2:
        raise HampyeongContractError(f"page {page} header rows changed")
    first = header_rows[0].find_all("th", recursive=False)
    if (
        len(first) != 8
        or any(_clean(node.get("rowspan")) != "2" for node in first[:6])
        or _clean(first[6].get("colspan")) != "2"
        or _clean(first[7].get("rowspan")) != "2"
    ):
        raise HampyeongContractError(f"page {page} header spans changed")
    _validate_check_date_script(soup, page)
    return table


def _validate_pager(soup: BeautifulSoup, page: int, has_rows: bool) -> None:
    pagers = soup.select("div.pagination")
    if not has_rows:
        # An entirely empty catalogue has no pager.  Once prior data pages
        # exist, the live endpoint keeps a backward-only pager on the first
        # explicit empty page.  It must neither mark that page active nor
        # expose a link to the sentinel/future pages.
        if page == 1 and not pagers:
            return
        pager = _one(pagers, f"page {page} empty-sentinel pagination")
        if pager.select("a.active"):
            raise HampyeongContractError(
                f"page {page} empty sentinel unexpectedly has an active page"
            )
        links = pager.select("a[href]")
        if not links:
            raise HampyeongContractError(
                f"page {page} empty sentinel pager has no backward links"
            )
        for link in links:
            absolute = urljoin(HAMPYEONG_CANONICAL_URL, _clean(link.get("href")))
            try:
                parsed, query = _query_map(absolute)
                port = parsed.port
            except (ValueError, HampyeongContractError) as exc:
                raise HampyeongContractError(
                    f"page {page} empty sentinel pager link changed"
                ) from exc
            target = query.get("movePage", "")
            if (
                parsed.scheme != "https"
                or parsed.hostname != HAMPYEONG_HOST
                or port is not None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path != HAMPYEONG_LIST_PATH
                or parsed.params
                or parsed.fragment
                or set(query) != {"movePage", "action", "pageID"}
                or query.get("action") != "list"
                or query.get("pageID") != HAMPYEONG_PAGE_ID
                or not _IDENTITY_RE.fullmatch(target)
                or int(target) >= page
            ):
                raise HampyeongContractError(
                    f"page {page} empty sentinel pager escaped prior pages"
                )
        return
    pager = _one(pagers, f"page {page} pagination")
    active = _one(pager.select("a.active[href]"), f"page {page} active pager")
    if _clean(active.get_text(" ", strip=True)) != str(page):
        raise HampyeongContractError(f"page {page} active pager changed")
    for link in pager.select("a[href]"):
        absolute = urljoin(HAMPYEONG_CANONICAL_URL, _clean(link.get("href")))
        try:
            parsed, query = _query_map(absolute)
            port = parsed.port
        except (ValueError, HampyeongContractError) as exc:
            raise HampyeongContractError(f"page {page} pager link changed") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != HAMPYEONG_HOST
            or port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != HAMPYEONG_LIST_PATH
            or parsed.params
            or parsed.fragment
            or set(query) != {"movePage", "action", "pageID"}
            or query.get("action") != "list"
            or query.get("pageID") != HAMPYEONG_PAGE_ID
            or not _IDENTITY_RE.fullmatch(query.get("movePage", ""))
        ):
            raise HampyeongContractError(f"page {page} pager escaped catalogue")
    active_page = _query_map(urljoin(HAMPYEONG_CANONICAL_URL, active["href"]))[1][
        "movePage"
    ]
    if active_page != str(page):
        raise HampyeongContractError(f"page {page} active pager URL changed")


def _date_range(value: Any, label: str) -> tuple[str, str]:
    tokens = _ISO_DATE_RE.findall(_clean(value))
    if len(tokens) != 2:
        raise HampyeongContractError(f"{label} date range changed")
    try:
        start = date.fromisoformat(tokens[0])
        end = date.fromisoformat(tokens[1])
    except ValueError as exc:
        raise HampyeongContractError(f"{label} contains invalid date") from exc
    if end < start:
        raise HampyeongContractError(f"{label} date range is reversed")
    return start.isoformat(), end.isoformat()


def _timestamp(value: Any, label: str) -> tuple[datetime, str]:
    cleaned = _clean(value)
    match = _TIMESTAMP_RE.fullmatch(cleaned)
    if not match:
        raise HampyeongContractError(f"{label} timestamp changed")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise HampyeongContractError(f"{label} timestamp is invalid") from exc
    return parsed, cleaned


def _validate_detail_href(value: Any) -> tuple[str, str]:
    absolute = urljoin(HAMPYEONG_CANONICAL_URL, _clean(value))
    try:
        parsed, query = _query_map(absolute)
        port = parsed.port
    except (ValueError, HampyeongContractError) as exc:
        raise HampyeongContractError("malformed detail URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != HAMPYEONG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != HAMPYEONG_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"pageID", "action", "seq"}
        or query.get("pageID") != HAMPYEONG_PAGE_ID
        or query.get("action") != "view"
        or not _IDENTITY_RE.fullmatch(query.get("seq", ""))
    ):
        raise HampyeongContractError("detail URL escaped catalogue")
    identity = query["seq"]
    return identity, hampyeong_detail_url(identity)


def _validate_application_href(value: Any, identity: str) -> str:
    absolute = urljoin(HAMPYEONG_CANONICAL_URL, _clean(value))
    try:
        parsed, query = _query_map(absolute)
        port = parsed.port
    except (ValueError, HampyeongContractError) as exc:
        raise HampyeongContractError(f"course {identity} malformed application URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != HAMPYEONG_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != HAMPYEONG_APPLY_PATH
        or parsed.params
        or parsed.fragment
        or query
        != {"pageID": HAMPYEONG_PAGE_ID, "action": "insert", "eseq": identity}
    ):
        raise HampyeongContractError(
            f"course {identity} application URL escaped course identity"
        )
    return hampyeong_application_url(identity)


def _parse_control(
    node: Any,
    *,
    identity: str,
    step: str,
    detail: bool,
) -> tuple[str, str, str]:
    expected_classes = {"btn", "btn_apply"} if detail else {"table_view"}
    if set(node.get("class") or []) != expected_classes:
        raise HampyeongContractError(f"course {identity} application class changed")
    if _clean(node.get_text(" ", strip=True)) != "신청하기":
        raise HampyeongContractError(f"course {identity} application label changed")
    href = _clean(node.get("href"))
    onclick = _clean(node.get("onclick"))
    if href == "#none":
        status = _INERT_CONTROL_STATUS.get(onclick)
        if status is None:
            raise HampyeongContractError(
                f"course {identity} inert application control changed"
            )
        if step == "1" and status != "SCHEDULED":
            raise HampyeongContractError(f"course {identity} step/status mismatch")
        if step == "4" and status != "CLOSED":
            raise HampyeongContractError(f"course {identity} step/status mismatch")
        if step == "3":
            raise HampyeongContractError(f"course {identity} open step is inert")
        return status, "", "inert_alert"
    if step not in {"2", "3"}:
        raise HampyeongContractError(f"course {identity} inactive step has live action")
    if detail:
        if onclick:
            raise HampyeongContractError(
                f"course {identity} detail application onclick changed"
            )
    elif onclick != "return checkClassDate(this);":
        raise HampyeongContractError(
            f"course {identity} list application guard changed"
        )
    return "OPEN", _validate_application_href(href, identity), "course_bound"


def _parse_list_page(
    soup: BeautifulSoup,
    page: int,
) -> tuple[list[dict[str, Any]], bool]:
    table = _validate_page_contract(soup, page)
    rows: list[dict[str, Any]] = []
    explicit_empty = False
    for tr in table.select(":scope > tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) == 1:
            if (
                _clean(cells[0].get("colspan")) == "9"
                and _clean(cells[0].get_text(" ", strip=True))
                == "등록된 교육 내용이 없습니다."
            ):
                explicit_empty = True
                continue
            raise HampyeongContractError(f"page {page} unknown empty marker")
        if len(cells) != 9:
            raise HampyeongContractError(f"page {page} course row cell count changed")
        detail_link = _one(
            cells[1].select(":scope > a[href]"), f"page {page} course detail link"
        )
        identity, raw_url = _validate_detail_href(detail_link.get("href"))
        title = _clean(detail_link.get_text(" ", strip=True))
        if not title or _normalized(cells[1].get_text(" ", strip=True)) != _normalized(title):
            raise HampyeongContractError(f"course {identity} title changed")
        category = _clean(cells[0].get_text(" ", strip=True))
        if category not in {label for value, label in _CATEGORY_OPTIONS if value != "0"}:
            raise HampyeongContractError(f"course {identity} unknown category")
        venue = _clean(cells[2].get_text(" ", strip=True))
        if not venue:
            raise HampyeongContractError(f"course {identity} venue is empty")
        current_text = _clean(cells[3].get_text(" ", strip=True))
        total_text = _clean(cells[4].get_text(" ", strip=True))
        if not _INTEGER_RE.fullmatch(current_text) or not _INTEGER_RE.fullmatch(total_text):
            raise HampyeongContractError(f"course {identity} capacity changed")
        current, total = int(current_text), int(total_text)
        raw_period = _clean(cells[6].get_text(" ", strip=True))
        start, end = _date_range(raw_period, f"course {identity} operating")
        schedule = _clean(cells[7].get_text(" ", strip=True))
        if not schedule:
            raise HampyeongContractError(f"course {identity} schedule is empty")
        step = _clean(tr.get("data-step"))
        if step not in {"1", "2", "3", "4"}:
            raise HampyeongContractError(f"course {identity} unknown source step")
        application_start_dt, application_start = _timestamp(
            tr.get("data-start"), f"course {identity} application start"
        )
        application_stop_dt, application_stop = _timestamp(
            tr.get("data-stop"), f"course {identity} application stop"
        )
        if application_stop_dt < application_start_dt:
            raise HampyeongContractError(
                f"course {identity} application window is reversed"
            )
        control = _one(
            cells[8].select(":scope > a"), f"course {identity} application control"
        )
        status, application_url, control_kind = _parse_control(
            control, identity=identity, step=step, detail=False
        )
        rows.append(
            {
                "source_page": page,
                "identity": identity,
                "title": title,
                "category": category,
                "venue": venue,
                "capacity_current": current,
                "capacity_total": total,
                "raw_period": raw_period,
                "start_date": start,
                "end_date": end,
                "schedule": schedule,
                "source_step": step,
                "source_status": status,
                "status": status,
                "application_start": application_start,
                "application_stop": application_stop,
                "application_url": application_url,
                "control_kind": control_kind,
                "raw_url": raw_url,
            }
        )
    if rows and explicit_empty:
        raise HampyeongContractError(f"page {page} mixes rows and empty marker")
    _validate_pager(soup, page, bool(rows))
    return rows, explicit_empty


def _row_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    keys = (
        "identity",
        "title",
        "category",
        "venue",
        "capacity_current",
        "capacity_total",
        "raw_period",
        "start_date",
        "end_date",
        "schedule",
        "source_step",
        "source_status",
        "application_start",
        "application_stop",
        "application_url",
        "control_kind",
        "raw_url",
    )
    return tuple(row.get(key) for key in keys)


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_row_signature(row) for row in rows)


def _detail_pairs(table: Any, identity: str) -> dict[str, str]:
    result: dict[str, str] = {}
    order: list[str] = []
    for tr in table.select(":scope > tbody > tr"):
        nodes = tr.find_all(["th", "td"], recursive=False)
        if len(nodes) % 2 or any(
            node.name != ("th" if index % 2 == 0 else "td")
            for index, node in enumerate(nodes)
        ):
            raise HampyeongContractError(f"course {identity} detail pairing changed")
        for index in range(0, len(nodes), 2):
            key = _clean(nodes[index].get_text(" ", strip=True))
            if not key or key in result:
                raise HampyeongContractError(f"course {identity} duplicate detail field")
            order.append(key)
            result[key] = _clean(nodes[index + 1].get_text(" ", strip=True))
    if tuple(order) != _DETAIL_FIELDS:
        raise HampyeongContractError(f"course {identity} detail fields changed")
    return result


def _application_period(value: Any, identity: str) -> tuple[str, str, str]:
    cleaned = _clean(value)
    if cleaned == "연중":
        return "연중", "", ""
    matches = _KOREAN_DATE_RE.findall(cleaned)
    if len(matches) != 2 or not matches[0][0]:
        raise HampyeongContractError(f"course {identity} application period changed")
    first_year = int(matches[0][0])
    second_year = int(matches[1][0] or first_year)
    try:
        start = date(first_year, int(matches[0][1]), int(matches[0][2]))
        end = date(second_year, int(matches[1][1]), int(matches[1][2]))
    except ValueError as exc:
        raise HampyeongContractError(
            f"course {identity} application period is invalid"
        ) from exc
    if end < start:
        raise HampyeongContractError(f"course {identity} application period is reversed")
    return f"{start.isoformat()} ~ {end.isoformat()}", start.isoformat(), end.isoformat()


def _detail_integer(value: Any, identity: str, label: str) -> int:
    match = _DETAIL_INTEGER_RE.fullmatch(_clean(value))
    if not match:
        raise HampyeongContractError(f"course {identity} detail {label} changed")
    return int(match.group(1))


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
        raise HampyeongContractError("persisted raw-field allowlist changed")
    lowered = {
        _clean(value).casefold()
        for value in _walk_values(row)
        if isinstance(value, str)
    }
    if lowered & {value.casefold() for value in _FORBIDDEN_PERSISTED_KEYS}:
        raise HampyeongContractError("forbidden private/detail field reached output")
    for value in _walk_values(row):
        if isinstance(value, str) and _contains_pii(value):
            raise HampyeongContractError("phone/email reached persisted allowlist")
    if row.get("description") != row.get("title"):
        raise HampyeongContractError("description must contain title only")
    if bool(row.get("application_url")) != bool(row.get("reservation_available")):
        raise HampyeongContractError("application availability is inconsistent")


def _branch_code() -> str:
    digest = hashlib.sha1(HAMPYEONG_BRANCH.encode("utf-8")).hexdigest()[:12].upper()
    return f"{HAMPYEONG_PROVIDER}:LIFELONG:{digest}"[:100]


def _parse_detail(
    parent: Mapping[str, Any],
    soup: BeautifulSoup,
    target: Any,
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    title = _one(soup.select("head > title"), f"course {identity} detail title")
    if _clean(title.get_text(" ", strip=True)) != "강좌정보 | 함평군 평생학습":
        raise HampyeongContractError(f"course {identity} detail owner changed")
    table = _one(soup.select("table.basic_table"), f"course {identity} detail table")
    caption = _one(table.select(":scope > caption"), f"course {identity} detail caption")
    if _clean(caption.get_text(" ", strip=True)) != _DETAIL_CAPTION:
        raise HampyeongContractError(f"course {identity} detail caption changed")
    pairs = _detail_pairs(table, identity)
    comparisons = (
        ("강좌명", "title"),
        ("구분", "category"),
        ("교육장소", "venue"),
        ("교육기간", "raw_period"),
        ("운영시간", "schedule"),
    )
    for field, parent_key in comparisons:
        if _normalized(pairs[field]) != _normalized(parent.get(parent_key)):
            raise HampyeongContractError(
                f"course {identity} detail/list {field} mismatch"
            )
    current = _detail_integer(pairs["신청인원"], identity, "applicants")
    total = _detail_integer(pairs["모집인원"], identity, "capacity")
    if (current, total) != (
        parent.get("capacity_current"),
        parent.get("capacity_total"),
    ):
        raise HampyeongContractError(f"course {identity} detail/list capacity mismatch")
    start, end = _date_range(pairs["교육기간"], f"course {identity} detail operating")
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise HampyeongContractError(f"course {identity} detail/list period mismatch")
    apply_period, apply_start, apply_end = _application_period(
        pairs["신청기간"], identity
    )
    control = _one(
        soup.select("a.btn.btn_apply"), f"course {identity} detail application control"
    )
    status, application_url, _kind = _parse_control(
        control,
        identity=identity,
        step=_clean(parent.get("source_step")),
        detail=True,
    )
    if status != parent.get("status") or application_url != parent.get(
        "application_url"
    ):
        raise HampyeongContractError(
            f"course {identity} detail/list application control mismatch"
        )

    open_now = status == "OPEN"
    row = {
        "provider": HAMPYEONG_PROVIDER,
        "provider_course_id": f"{HAMPYEONG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": HAMPYEONG_BRANCH,
        "branch_code": _branch_code(),
        "provider_organizer": HAMPYEONG_BRANCH,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": apply_period,
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": status,
        "category": "교육",
        "program_type": "평생학습 강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_complete_paginated_table+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": _clean(parent.get("schedule")),
        "room": _clean(parent.get("venue")),
        "venue": _clean(parent.get("venue")),
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_current": current,
        "capacity_total": total,
        "capacity_remaining": max(0, total - current),
        "application_method": "온라인 수강신청",
        "application_methods": ["온라인"],
        "reservation_available": open_now,
        "application_url": application_url if open_now else "",
        "application_type": "ONLINE_RESERVATION" if open_now else "",
        "raw_url": _clean(parent.get("raw_url")),
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "source_identity": identity,
            "source_page": parent.get("source_page"),
            "source_category": parent.get("category"),
            "source_step": parent.get("source_step"),
            "source_status": status,
            "source_application_start": parent.get("application_start"),
            "source_application_stop": parent.get("application_stop"),
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
    if _clean(title.get_text(" ", strip=True)) != "Move":
        raise HampyeongContractError(f"course {identity} login gate title changed")
    if soup.select("form"):
        raise HampyeongContractError(f"course {identity} login gate exposed a form")
    scripts = [
        node.get_text(" ", strip=True)
        for node in soup.select("script")
        if "로그인이 필요합니다." in node.get_text()
    ]
    if len(scripts) != 1:
        raise HampyeongContractError(f"course {identity} login gate changed")
    normalized = re.sub(r"\s+", "", scripts[0])
    expected = (
        "alert('로그인이필요합니다.');window.document.location.href="
        f"'{HAMPYEONG_LOGIN_PATH}?action=login&pageID={HAMPYEONG_PAGE_ID}';"
    )
    if normalized != expected:
        raise HampyeongContractError(
            f"course {identity} login gate destination changed"
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
        "sentinel_page": 0,
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
        "source_step_counts": {},
        "source_status_counts": {},
        "source_application_link_count": 0,
        "application_control_count": 0,
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
        "exact_branch_name": HAMPYEONG_BRANCH,
        "configured_collection_error": "",
    }


def _failure(message: str, **updates: Any) -> dict[str, Any]:
    meta = _base_meta()
    meta.update(updates)
    meta["configured_collection_error"] = message
    return meta


def collect_hampyeong_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 40,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    today: Optional[date | datetime | str] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return the complete current/future Hampyeong course snapshot."""

    if not is_hampyeong_target(target):
        return [], HAMPYEONG_PARSER, _failure(
            "target does not match the canonical Hampyeong lifelong catalogue"
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
        ):
            raise ValueError
        if allowed_pages < 0 or allowed_details < 0 or timeout_value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return [], HAMPYEONG_PARSER, _failure(
            "max_pages/detail_limit/timeout/today are invalid"
        )
    if allowed_pages < 3:
        return [], HAMPYEONG_PARSER, _failure(
            f"max_pages cap allows {allowed_pages}; first page, sentinel and "
            "page-one recheck require at least 3",
            source_cap_reached=True,
        )

    try:
        client = _Client(
            timeout=timeout_value,
            fetcher=fetcher or _default_fetcher,
            session_factory=session_factory or _default_session_factory,
        )
    except Exception as exc:
        return [], HAMPYEONG_PARSER, _failure(
            f"client setup: {type(exc).__name__}: {exc}"
        )
    errors: list[str] = []
    source_cap_reached = False
    all_rows: list[dict[str, Any]] = []
    page_counts: dict[int, int] = {}
    first_rows: list[dict[str, Any]] = []
    data_pages = 0
    sentinel_page = 0
    list_requests = 0
    list_rechecks = 0
    detail_attempts = 0
    detail_pages = 0
    gate_attempts = 0
    gate_pages = 0

    try:
        page = 1
        prior_partial = False
        while not errors:
            # Reserve one request for the required page-one recheck.
            if list_requests + 2 > allowed_pages:
                source_cap_reached = True
                errors.append(
                    f"max_pages cap reached after {list_requests} list requests "
                    "before explicit sentinel and page-one recheck"
                )
                break
            try:
                soup = client.get(hampyeong_list_url(page))
                list_requests += 1
                rows, explicit_empty = _parse_list_page(soup, page)
            except Exception as exc:
                errors.append(f"page {page}: {type(exc).__name__}: {exc}")
                break
            if explicit_empty:
                if rows:
                    errors.append(f"page {page} mixes data and sentinel")
                sentinel_page = page
                page_counts[page] = 0
                break
            if not rows:
                errors.append(f"page {page} lacks rows and explicit empty marker")
                break
            if prior_partial:
                errors.append(f"page {page} follows a partial data page")
                break
            if len(rows) > HAMPYEONG_PAGE_SIZE:
                errors.append(f"page {page} exceeds page size")
                break
            prior_partial = len(rows) < HAMPYEONG_PAGE_SIZE
            if page == 1:
                first_rows = rows
            page_counts[page] = len(rows)
            all_rows.extend(rows)
            data_pages += 1
            page += 1

        if not errors:
            try:
                recheck = client.get(HAMPYEONG_CANONICAL_URL)
                list_requests += 1
                rechecked_rows, recheck_empty = _parse_list_page(recheck, 1)
                list_rechecks = 1
                expected_empty = sentinel_page == 1 and not first_rows
                if recheck_empty != expected_empty or _page_signature(
                    rechecked_rows
                ) != _page_signature(first_rows):
                    raise HampyeongContractError("page-one recheck changed")
            except Exception as exc:
                errors.append(f"page-one recheck: {type(exc).__name__}: {exc}")

        identities = [_clean(row.get("identity")) for row in all_rows]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate source identities")
        if identities and any(
            int(left) <= int(right) for left, right in zip(identities, identities[1:])
        ):
            errors.append("source identities are not strictly descending")

        for row in all_rows:
            if row.get("source_step") != "2":
                continue
            start_day = datetime.fromisoformat(_clean(row.get("application_start"))).date()
            stop_day = datetime.fromisoformat(_clean(row.get("application_stop"))).date()
            status = row.get("status")
            if status == "OPEN" and not (start_day <= cutoff <= stop_day):
                errors.append(f"course {row.get('identity')} step-2 open window mismatch")
            elif status == "SCHEDULED" and cutoff > start_day:
                errors.append(
                    f"course {row.get('identity')} step-2 scheduled window mismatch"
                )
            elif status == "CLOSED" and cutoff < stop_day:
                errors.append(f"course {row.get('identity')} step-2 closed window mismatch")

        current_parents: list[dict[str, Any]] = []
        expired_count = 0
        for row in all_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(f"course {row.get('identity')} invalid end date")
                continue
            if end < cutoff:
                expired_count += 1
            else:
                current_parents.append(row)
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
                    soup = client.get(_clean(parent.get("raw_url")))
                    row = _parse_detail(parent, soup, target)
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

        required_list_requests = data_pages + 2 if sentinel_page else 0
        snapshot_complete = not errors
        pagination_complete = bool(
            snapshot_complete
            and sentinel_page == data_pages + 1
            and list_rechecks == 1
            and list_requests == required_list_requests
            and len(all_rows) == sum(
                count for number, count in page_counts.items() if number < sentinel_page
            )
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
        step_counts = Counter(_clean(row.get("source_step")) for row in all_rows)
        status_counts = Counter(_clean(row.get("status")) for row in all_rows)
        source_application_links = sum(
            bool(row.get("application_url")) for row in all_rows
        )
        meta = _base_meta()
        meta.update(
            {
                "pages": client.requests,
                "request_count": client.requests,
                "sessions_created": client.sessions_created,
                "source_total": len(all_rows),
                "source_rows": len(all_rows),
                "page_counts": page_counts,
                "data_pages": data_pages,
                "sentinel_page": sentinel_page,
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
                "source_step_counts": dict(step_counts),
                "source_status_counts": dict(status_counts),
                "source_application_link_count": source_application_links,
                "application_control_count": open_current,
                "duplicate_count": duplicate_count,
                "pagination_detected": data_pages > 1,
                "pagination_complete": pagination_complete,
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": full_snapshot_validated,
                "source_cap_reached": source_cap_reached,
                "no_current_data": bool(snapshot_complete and not current_parents),
                "no_current_reason": (
                    "the complete Hampyeong lifelong catalogue is empty"
                    if snapshot_complete and not all_rows
                    else (
                        "all rows in the complete Hampyeong lifelong catalogue have ended"
                        if snapshot_complete and not current_parents
                        else ""
                    )
                ),
                "configured_collection_error": "; ".join(errors),
            }
        )
        if errors:
            return [], HAMPYEONG_PARSER, meta
        return result, HAMPYEONG_PARSER, meta
    finally:
        client.close()


collect = collect_hampyeong_education_courses


__all__ = [
    "HAMPYEONG_BRANCH",
    "HAMPYEONG_CANDIDATE_AUDIT",
    "HAMPYEONG_CANDIDATE_ID",
    "HAMPYEONG_CANONICAL_URL",
    "HAMPYEONG_DISCOVERY_AUDIT",
    "HAMPYEONG_EDUCATION_LIBRARY_BRANCH",
    "HAMPYEONG_EDUCATION_LIBRARY_PROVIDER",
    "HAMPYEONG_EXISTING_HOME_URL",
    "HAMPYEONG_HOST",
    "HAMPYEONG_MUNICIPALITY_CODE",
    "HAMPYEONG_MUNICIPALITY_NAME",
    "HAMPYEONG_OWNER_BOUNDARY_AUDIT",
    "HAMPYEONG_PAGE_ID",
    "HAMPYEONG_PARSER",
    "HAMPYEONG_PII_FIELDS_DISCARDED",
    "HAMPYEONG_PROVIDER",
    "HAMPYEONG_REGISTERED_CANDIDATE_URL",
    "HampyeongContractError",
    "collect",
    "collect_hampyeong_education_courses",
    "hampyeong_application_url",
    "hampyeong_detail_url",
    "hampyeong_list_url",
    "is_hampyeong_target",
    "is_target",
]
