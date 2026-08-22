"""Fail-closed education collectors for the Tongyeong municipality scope.

The discovery candidate on ``tylib.gne.go.kr`` belongs to the Gyeongsangnam-
do Office of Education, not Tongyeong City.  Tongyeong City publishes two
other, disjoint official ledgers: the city lifelong-learning catalogue and
the municipal-library culture-course catalogue.  This module keeps those
owners separate while exposing one exact dispatcher for operational wiring.

Every collector walks the complete advertised ledger before opening only
current/future details.  A snapshot is discarded when its declared last page,
post-last sentinel/clamp, stable boundaries, official identities, detail
identities, or public application controls disagree.  Application and private
registration-check endpoints are identity-validated but never requested.

Rows are constructed from explicit structured-field allowlists.  Instructor,
staff/contact, applicant, attachment/plan, preparation, free-body, form and
source-HTML values are never persisted.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


TONGYEONG_MUNICIPALITY_CODE = "4822000000"
TONGYEONG_MUNICIPALITY_NAME = "경상남도 통영시"

TONGYEONG_GNE_PROVIDER = "MUNI_TYLIB_GNE_GO_KR_7D159AC1"
TONGYEONG_GNE_CANDIDATE_ID = "MUNI_IR_AA1D1E424C3A"
TONGYEONG_GNE_MENU_ALIAS_CANDIDATE_ID = "MUNI_IR_671EB1183AE7"
TONGYEONG_GNE_HOST = "tylib.gne.go.kr"
TONGYEONG_GNE_BRANCH = "경상남도교육청 통영도서관"
TONGYEONG_GNE_ADDRESS = "경상남도 통영시 봉수로 37"
TONGYEONG_GNE_MENU_URL = (
    "https://tylib.gne.go.kr/menu.es?mid=b20401000000"
)
TONGYEONG_GNE_LIST_URL = (
    "https://tylib.gne.go.kr/usr_gne/lec_list.es?"
    "mid=b20402000000&cate_no=10"
)
TONGYEONG_GNE_DETAIL_PATH = "/usr_gne/lec_v.es"
TONGYEONG_GNE_REGISTER_PATH = "/usr_gne/register.es"
TONGYEONG_GNE_PRIVATE_FORM_PATH = "/usr_gne/lec_reqin.es"
TONGYEONG_GNE_PARSER = (
    "tongyeong_gne_library_complete_pages+empty_post_last_sentinel+"
    "stable_first_last_and_sentinel+current_details+opening_day_status_boundary+"
    "visible_application_and_register_identity+single_facility_branch+pii_allowlist"
)

TONGYEONG_CITY_PROVIDER = "MUNI_WWW_TONGYEONG_GO_KR_DC5CDBF8"
TONGYEONG_CITY_CANDIDATE_ID = "MUNI_IR_8F5B4B3D8FFB"
TONGYEONG_CITY_HOME_ALIAS_CANDIDATE_ID = "MUNI_IR_96089165E588"
TONGYEONG_CITY_HOST = "www.tongyeong.go.kr"
TONGYEONG_CITY_HOME_URL = "https://www.tongyeong.go.kr/tylearning.web"
TONGYEONG_CITY_LIST_PATH = "/tylearning/04266/04267/05286.web"
TONGYEONG_CITY_LIST_URL = (
    "https://www.tongyeong.go.kr" + TONGYEONG_CITY_LIST_PATH
)
TONGYEONG_CITY_PAGE_SIZE = 10
TONGYEONG_CITY_PARSER = (
    "tongyeong_city_lifelong_declared_total+all_pages+exact_last_clamp+"
    "stable_first_last+current_details+institution_branches+"
    "identity_bound_application_control+pii_allowlist"
)

TONGYEONG_LIBRARY_PROVIDER = "MUNI_WWW_TONGYEONGLIB_OR_KR_F370713B"
TONGYEONG_LIBRARY_HOST = "www.tongyeonglib.or.kr"
TONGYEONG_LIBRARY_PATH = "/library/index.php"
TONGYEONG_LIBRARY_LIST_URL = (
    "https://www.tongyeonglib.or.kr/library/index.php?"
    "g_page=culture&m_page=culture02"
)
TONGYEONG_LIBRARY_PAGE_SIZE = 10
TONGYEONG_LIBRARY_RECEIVE_ACTION = "lecture_receive_form"
TONGYEONG_LIBRARY_PRIVATE_CHECK_ACTION = "lecture_cancel_form"
TONGYEONG_LIBRARY_PARSER = (
    "tongyeong_municipal_library_declared_last+all_pages+empty_post_last_"
    "sentinel+stable_first_last_and_sentinel+late_current_rows+current_"
    "details+identity_bound_receive_without_private_check_fetch+facility_"
    "branches+pii_allowlist"
)

TONGYEONG_PARSER = "tongyeong_exact_owner_education_dispatch"
TONGYEONG_FETCH_ATTEMPTS = 2
TONGYEONG_MAX_HTML_BYTES = 4_000_000

TONGYEONG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    TONGYEONG_GNE_PROVIDER: {
        "owner": "경상남도교육청 통영도서관",
        "decision": "separate_education_office_library_owner",
        "candidate_id": TONGYEONG_GNE_CANDIDATE_ID,
        "canonical_catalogue": TONGYEONG_GNE_LIST_URL,
        "aliases": (TONGYEONG_GNE_MENU_URL,),
        "branch_scope": (TONGYEONG_GNE_BRANCH,),
    },
    TONGYEONG_CITY_PROVIDER: {
        "owner": "통영시 평생학습도시",
        "decision": "city_lifelong_and_resident_centre_catalogue",
        "candidate_id": TONGYEONG_CITY_CANDIDATE_ID,
        "canonical_catalogue": TONGYEONG_CITY_LIST_URL,
        "aliases": (TONGYEONG_CITY_HOME_URL,),
        "audited_institutions": ("통영시청", "읍면동 주민자치센터"),
    },
    TONGYEONG_LIBRARY_PROVIDER: {
        "owner": "통영시립도서관",
        "decision": "separate_city_owned_municipal_library_catalogue",
        "canonical_catalogue": TONGYEONG_LIBRARY_LIST_URL,
        "branch_tabs": ("시립", "충무", "꿈이랑", "작은"),
        "current_exact_branches": (
            "통영시립도서관",
            "통영시립충무도서관",
            "꿈이랑도서관",
            "더팰리스작은도서관",
            "안정작은도서관",
        ),
    },
}

TONGYEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "municipality_code": TONGYEONG_MUNICIPALITY_CODE,
    "gne_library": {
        "source_total": 9,
        "current_or_future": 9,
        "declared_last_page": 1,
        "post_last_page": 2,
        "source_status_counts": {"모집중": 2, "준비중": 6, "교육중": 1},
        "application_controls": 2,
        "branch": TONGYEONG_GNE_BRANCH,
    },
    "city_lifelong": {
        "source_total": 191,
        "declared_last_page": 20,
        "post_last_mode": "exact_last_page_clamp",
        "source_institution_counts": {
            "통영시청": 52,
            "읍면동 주민자치센터": 139,
        },
        "current_or_future": 2,
        "current_branch_counts": {"통영시청": 2},
    },
    "municipal_library": {
        "source_total": 266,
        "declared_last_page": 27,
        "post_last_page": 28,
        "current_or_future": 31,
        "late_current_pages": {5: 3, 26: 1},
        "current_status_counts": {
            "신청하기": 6,
            "대기자신청": 3,
            "접수마감": 22,
        },
        "current_branch_counts": {
            "통영시립도서관": 12,
            "통영시립충무도서관": 11,
            "꿈이랑도서관": 4,
            "더팰리스작은도서관": 2,
            "안정작은도서관": 2,
        },
        "official_identity_anomaly": (
            "(lgCode=3,leCode=11760) and (lgCode=8,leCode=11759) "
            "have duplicate-looking content but distinct official identities; "
            "both are preserved"
        ),
    },
}

TONGYEONG_PII_FIELDS_NEVER_PERSISTED = (
    "강사/강사명/초빙강사",
    "담당자명/담당자 연락처",
    "신청자명/전화번호/이메일",
    "강의소개/자유본문/홍보 이미지",
    "첨부파일/계획서/준비물",
    "신청 및 등록확인 form payload",
    "source_html/raw_html/raw detail pairs",
)


class TongyeongContractError(ValueError):
    """Raised when an official Tongyeong ledger violates its contract."""


Requester = Callable[
    [Any, str, int, Optional[Mapping[str, str]]],
    Any,
]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class _GnePage:
    requested_page: int
    declared_last: int
    rows: tuple[dict[str, Any], ...]
    structural_empty: bool


@dataclass(frozen=True)
class _CityPage:
    requested_page: int
    reported_page: int
    declared_last: int
    declared_total: int
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _LibraryPage:
    requested_page: int
    reported_page: Optional[int]
    declared_last: int
    rows: tuple[dict[str, Any], ...]
    structural_empty: bool


_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[.-](\d{1,2})[.-](\d{1,2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s.)]+[\d*]{3,4}[-\s]+[\d*]{4}|"
    r"0\d{1,3}[-\s.)]?\d{3,4}[-\s]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_GNE_ID_RE = re.compile(r"^[1-9]\d*$")
_LIBRARY_ID_RE = re.compile(r"^[1-9]\d*$")
_CITY_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*건이\s*있습니다\.?\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_GNE_APPLY_RE = re.compile(
    r"^\s*requestStudent\(\s*['\"](?P<id>[1-9]\d*)['\"]\s*,\s*"
    r"['\"]{2}\s*\)\s*;?\s*(?:return\s+false\s*;?)?\s*$"
)

_GNE_LIST_HEADERS = ("과정", "강좌명", "학습대상", "정원", "접수방법", "상태")
_CITY_LIST_HEADERS = (
    "기관",
    "강좌명",
    "모집/대기 인원",
    "접수기간",
    "강좌기간",
    "접수방법",
    "수강료",
    "상태",
)
_LIBRARY_LIST_HEADERS = (
    "도서관",
    "행사 / 강좌명 / 대상",
    "모집인원",
    "접수기간 / 수강일시",
    "접수현황",
)

_GNE_STATUS_MAP: Mapping[str, str] = {
    "모집중": "OPEN",
    "준비중": "SCHEDULED",
    "교육중": "CLOSED",
    "모집마감": "CLOSED",
    "마감": "CLOSED",
}
_CITY_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "신청하기": "OPEN",
    "접수대기": "SCHEDULED",
    "신청대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
}
_LIBRARY_STATUS_MAP: Mapping[str, str] = {
    "신청하기": "OPEN",
    "대기자신청": "OPEN",
    "대기중": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "신청마감": "CLOSED",
}

_GNE_SAFE_DETAIL_LABELS = frozenset(
    {
        "강좌명",
        "교육대상",
        "진행상태",
        "모집기간",
        "교육기간",
        "접수방법",
        "교육일시",
        "모집인원",
        "온라인접수",
        "후보",
        "강의장소",
        "재료비",
        "수강료",
    }
)
_GNE_DISCARDED_DETAIL_LABELS = frozenset(
    {
        "강사",
        "초빙강사",
        "담당자명",
        "담당자 연락처",
        "강의소개",
        "첨부파일",
        "참여자정보",
    }
)
_CITY_REQUIRED_DETAIL_LABELS = frozenset(
    {"교육기간", "접수기간", "수강료", "모집대상", "접수방법"}
)
_CITY_OPTIONAL_DETAIL_LABELS = frozenset({"교육시간", "교육장소"})
_CITY_DISCARDED_DETAIL_LABELS = frozenset({"첨부파일"})
_LIBRARY_SAFE_DETAIL_LABELS = frozenset(
    {
        "대상",
        "인터넷 모집인원",
        "현재신청자수",
        "대기자 모집인원",
        "수강료",
        "접수 기간",
        "강좌 기간",
        "강좌 일시",
        "강좌 장소",
    }
)
_LIBRARY_DISCARDED_DETAIL_LABELS = frozenset({"강사명", "계획서", "준비물"})

_FORBIDDEN_PERSISTED_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "manager",
        "manager_name",
        "contact",
        "contact_name",
        "phone",
        "email",
        "applicant_name",
        "attachments",
        "attachment_urls",
        "plan",
        "preparation",
        "detail_description",
        "detail_pairs",
        "source_html",
        "raw_html",
        "form_payload",
        "private_registration_url",
    }
)

_GNE_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_category",
        "source_status",
        "source_methods",
        "source_capacity",
        "source_material_fee",
        "detail_verified",
        "application_control_present",
        "application_control_identity",
        "private_application_form_present_but_not_requested",
        "service_family",
    }
)
_CITY_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "list_page",
        "source_institution",
        "source_status",
        "source_application_method",
        "detail_verified",
        "application_control_present",
        "application_control_identity",
        "service_family",
    }
)
_LIBRARY_SAFE_RAW_FIELDS = frozenset(
    {
        "identity",
        "lg_code",
        "le_code",
        "list_page",
        "source_branch_tab",
        "source_status",
        "source_capacity_current",
        "source_capacity_total",
        "source_waitlist_total",
        "detail_verified",
        "application_control_present",
        "application_control_identity",
        "private_registration_check_identity_verified_without_request",
        "source_identity_anomaly",
        "service_family",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _safe_https(parsed: Any, host: str) -> bool:
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == host
        and parsed.port is None
        and not parsed.params
        and not parsed.username
        and not parsed.password
    )


def is_tongyeong_gne_library_target(target: Any) -> bool:
    if _provider(target) != TONGYEONG_GNE_PROVIDER:
        return False
    parsed = urlparse(_target_url(target))
    if not _safe_https(parsed, TONGYEONG_GNE_HOST) or parsed.fragment:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == "/menu.es":
        return query == {"mid": ["b20401000000"]}
    return bool(
        parsed.path == "/usr_gne/lec_list.es"
        and query == {"mid": ["b20402000000"], "cate_no": ["10"]}
    )


def is_tongyeong_city_lifelong_target(target: Any) -> bool:
    if _provider(target) != TONGYEONG_CITY_PROVIDER:
        return False
    parsed = urlparse(_target_url(target))
    if not _safe_https(parsed, TONGYEONG_CITY_HOST) or parsed.fragment:
        return False
    return bool(
        (parsed.path == TONGYEONG_CITY_LIST_PATH and not parsed.query)
        or (parsed.path == "/tylearning.web" and not parsed.query)
    )


def is_tongyeong_municipal_library_target(target: Any) -> bool:
    if _provider(target) != TONGYEONG_LIBRARY_PROVIDER:
        return False
    parsed = urlparse(_target_url(target))
    if not _safe_https(parsed, TONGYEONG_LIBRARY_HOST) or parsed.fragment:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.path == TONGYEONG_LIBRARY_PATH
        and query == {"g_page": ["culture"], "m_page": ["culture02"]}
    )


def is_tongyeong_education_target(target: Any) -> bool:
    return bool(
        is_tongyeong_gne_library_target(target)
        or is_tongyeong_city_lifelong_target(target)
        or is_tongyeong_municipal_library_target(target)
    )


is_target = is_tongyeong_education_target


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _date_token(value: str, context: str) -> date:
    match = _DATE_RE.search(_clean(value))
    if not match:
        raise TongyeongContractError(f"{context} date missing")
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise TongyeongContractError(f"{context} date invalid") from exc


def _date_pair(value: str, context: str) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise TongyeongContractError(f"{context} requires exactly two dates")
    try:
        start, end = (date(*(int(part) for part in match)) for match in matches)
    except ValueError as exc:
        raise TongyeongContractError(f"{context} date invalid") from exc
    if end < start:
        raise TongyeongContractError(f"{context} is reversed")
    return start, end


def _date_span(value: str, context: str) -> tuple[date, date]:
    """Parse an official one-day value or an explicit start/end range."""

    matches = _DATE_RE.findall(_clean(value))
    if len(matches) == 1:
        try:
            only = date(*(int(part) for part in matches[0]))
        except ValueError as exc:
            raise TongyeongContractError(f"{context} date invalid") from exc
        return only, only
    return _date_pair(value, context)


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=TONGYEONG_FETCH_ATTEMPTS - 1,
        connect=TONGYEONG_FETCH_ATTEMPTS - 1,
        read=TONGYEONG_FETCH_ATTEMPTS - 1,
        status=TONGYEONG_FETCH_ATTEMPTS - 1,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _default_requester(
    session: Any,
    url: str,
    timeout: int,
    headers: Optional[Mapping[str, str]],
) -> Any:
    return session.get(url, timeout=timeout, headers=dict(headers or {}))


def _request_soup(
    session: Any,
    requester: Requester,
    url: str,
    timeout: int,
    headers: Optional[Mapping[str, str]] = None,
) -> BeautifulSoup:
    response = requester(session, url, timeout, headers)
    status = getattr(response, "status_code", None)
    if status != 200:
        raise TongyeongContractError(f"HTTP {status} for {url}")
    requested = urlparse(url)
    final = urlparse(_clean(getattr(response, "url", "")) or url)
    if (
        final.scheme.lower() != requested.scheme.lower()
        or (final.hostname or "").rstrip(".").lower()
        != (requested.hostname or "").rstrip(".").lower()
        or final.port != requested.port
        or final.path != requested.path
    ):
        raise TongyeongContractError(f"redirect escaped requested source for {url}")
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise TongyeongContractError(f"empty HTML for {url}")
    if len(content) > TONGYEONG_MAX_HTML_BYTES:
        raise TongyeongContractError(f"oversized HTML for {url}")
    response_headers = getattr(response, "headers", {}) or {}
    content_type = _clean(response_headers.get("Content-Type", ""))
    if content_type and not any(
        token in content_type.casefold() for token in ("html", "text/plain")
    ):
        raise TongyeongContractError(f"non-HTML response for {url}")
    return BeautifulSoup(bytes(content), "html.parser")


def _query_url(base: str, **values: Any) -> str:
    parsed = urlparse(base)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in values.items():
        query[key] = [str(value)]
    pairs = [(key, item) for key, items in query.items() for item in items]
    return parsed._replace(query=urlencode(pairs, doseq=True)).geturl()


def tongyeong_gne_page_url(page: int) -> str:
    return _query_url(TONGYEONG_GNE_LIST_URL, page=page)


def tongyeong_gne_detail_url(identity: str) -> str:
    return (
        "https://tylib.gne.go.kr/usr_gne/lec_v.es?"
        + urlencode(
            {
                "mid": "b20402000000",
                "gno": identity,
                "cate_no": "10",
            }
        )
    )


def tongyeong_city_page_url(page: int) -> str:
    if page == 1:
        return TONGYEONG_CITY_LIST_URL
    return _query_url(TONGYEONG_CITY_LIST_URL, cpage=page)


def tongyeong_city_detail_url(identity: str) -> str:
    return _query_url(TONGYEONG_CITY_LIST_URL, amode="view", idx=identity)


def tongyeong_library_page_url(page: int) -> str:
    return _query_url(TONGYEONG_LIBRARY_LIST_URL, page=page)


def tongyeong_library_detail_url(lg_code: str, le_code: str) -> str:
    return _query_url(
        TONGYEONG_LIBRARY_LIST_URL,
        libCho="TOL",
        act="lecture_view",
        lgCode=lg_code,
        leCode=le_code,
    )


def _absolute_same_source_url(
    href: str,
    base: str,
    host: str,
    path: str,
    context: str,
) -> str:
    absolute = urljoin(base, html.unescape(_clean(href)))
    parsed = urlparse(absolute)
    if not _safe_https(parsed, host) or parsed.path != path or parsed.fragment:
        raise TongyeongContractError(f"{context} escaped the official source")
    return absolute


def _table_by_headers(soup: BeautifulSoup, expected: tuple[str, ...]) -> Tag:
    normalized_expected = tuple(_normalized(item) for item in expected)
    matches: list[Tag] = []
    for table in soup.select("table"):
        header_row = table.select_one("thead tr")
        if header_row is None:
            continue
        actual = tuple(
            _normalized(cell.get_text(" ", strip=True))
            for cell in header_row.find_all(["th", "td"], recursive=False)
        )
        if actual == normalized_expected:
            matches.append(table)
    if len(matches) != 1:
        raise TongyeongContractError(
            f"expected one official table with headers {expected}, got {len(matches)}"
        )
    return matches[0]


def _branch_code(prefix: str, branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _fee_amount(value: str) -> Optional[int]:
    cleaned = _clean(value).replace(",", "")
    if cleaned in {"", "0", "0원", "무료", "없음"} or cleaned.startswith("무료"):
        return 0
    return None


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _privacy_errors(
    row: Mapping[str, Any], safe_raw_fields: frozenset[str]
) -> list[str]:
    errors: list[str] = []
    if set(row) & _FORBIDDEN_PERSISTED_KEYS:
        errors.append("forbidden PII/detail keys persisted")
    raw_fields = row.get("raw_fields")
    if not isinstance(raw_fields, Mapping) or not set(raw_fields) <= safe_raw_fields:
        errors.append("raw_fields exceeded the PII-safe allowlist")
    payload = repr(
        {
            key: value
            for key, value in row.items()
            if key not in {"raw_url", "application_url", "_source_end_date"}
        }
    )
    if _PHONE_RE.search(payload) or _EMAIL_RE.search(payload):
        errors.append("PII-like contact data persisted")
    if row.get("description") != row.get("title"):
        errors.append("free-form detail description persisted")
    if "<form" in payload.casefold() or "<script" in payload.casefold():
        errors.append("HTML/form payload persisted")
    return errors


def _base_meta(kind: str, parser: str, canonical_url: str) -> dict[str, Any]:
    return {
        "source_kind": kind,
        "parser": parser,
        "canonical_url": canonical_url,
        "municipality_code": TONGYEONG_MUNICIPALITY_CODE,
        "municipality_name": TONGYEONG_MUNICIPALITY_NAME,
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "data_pages": 0,
        "declared_last_page": 0,
        "sentinel_page": 0,
        "stability_rechecks": 0,
        "source_total": 0,
        "source_rows": 0,
        "identity_duplicate_count": 0,
        "current_candidate_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "archived_rows_skipped_before_detail": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "returned_count": 0,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "forbidden_application_endpoint_requests": 0,
        "pii_payload_persisted": False,
    }


def _validate_limits(timeout: int, max_pages: int, detail_limit: int) -> bool:
    return bool(
        not isinstance(timeout, bool)
        and isinstance(timeout, int)
        and timeout >= 1
        and not isinstance(max_pages, bool)
        and isinstance(max_pages, int)
        and max_pages >= 1
        and not isinstance(detail_limit, bool)
        and isinstance(detail_limit, int)
        and detail_limit >= 0
    )


def _finalize_rows(
    rows: list[dict[str, Any]],
    safe_raw_fields: frozenset[str],
    dedupe_rows: Optional[DedupeRows],
    errors: list[str],
) -> list[dict[str, Any]]:
    persistable: list[dict[str, Any]] = []
    for row in rows:
        errors.extend(_privacy_errors(row, safe_raw_fields))
        clean_row = dict(row)
        clean_row.pop("_source_end_date", None)
        persistable.append(clean_row)
    if errors:
        return []
    deduper = dedupe_rows or _dedupe_default
    try:
        result = list(deduper(persistable))
    except Exception as exc:
        errors.append(f"dedupe failed: {type(exc).__name__}: {_clean(exc)}")
        return []
    if len(result) != len(persistable):
        errors.append(
            "dedupe changed official identity cardinality "
            f"{len(persistable)} to {len(result)}"
        )
        return []
    return result


def _gne_badgeless_title(value: str) -> str:
    title = _clean(value)
    while True:
        updated = re.sub(
            r"^\[(?:무료|유료|일회성특강)\]\s*", "", title
        ).strip()
        if updated == title:
            return title
        title = updated


def _gne_declared_last(soup: BeautifulSoup) -> int:
    pages: list[int] = []
    for node in soup.select("a[onclick*='goPage'], a[href*='page=']"):
        onclick = _clean(node.get("onclick", ""))
        match = re.search(r"goPage\(\s*['\"]?(\d+)", onclick)
        if match:
            pages.append(int(match.group(1)))
            continue
        href = html.unescape(_clean(node.get("href", "")))
        parsed = urlparse(urljoin(TONGYEONG_GNE_LIST_URL, href))
        if parsed.path != "/usr_gne/lec_list.es":
            continue
        raw_page = parse_qs(parsed.query).get("page", [])
        if len(raw_page) == 1 and raw_page[0].isdigit():
            pages.append(int(raw_page[0]))
    if not pages or min(pages) < 1:
        raise TongyeongContractError("GNE pager did not declare a valid page")
    return max(pages)


def _gne_period_from_cell(text: str, label: str, context: str) -> str:
    cleaned = _clean(text)
    match = re.search(
        rf"{re.escape(label)}\s*:\s*"
        r"(20\d{2}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?)\s*~\s*"
        r"(20\d{2}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?)",
        cleaned,
    )
    if not match:
        raise TongyeongContractError(f"{context} {label} missing")
    return f"{_clean(match.group(1))} ~ {_clean(match.group(2))}"


def _gne_parse_list_row(row: Tag, requested_page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 6:
        raise TongyeongContractError(
            f"GNE page {requested_page} row requires six cells"
        )
    detail_links = cells[1].select("a[href*='/usr_gne/lec_v.es']")
    detail_links.extend(cells[5].select("a[href*='/usr_gne/lec_v.es']"))
    identities: set[str] = set()
    for anchor in detail_links:
        absolute = _absolute_same_source_url(
            anchor.get("href", ""),
            TONGYEONG_GNE_LIST_URL,
            TONGYEONG_GNE_HOST,
            TONGYEONG_GNE_DETAIL_PATH,
            "GNE detail link",
        )
        query = parse_qs(urlparse(absolute).query, keep_blank_values=True)
        identity_values = query.get("gno", [])
        if (
            len(identity_values) != 1
            or not _GNE_ID_RE.fullmatch(identity_values[0])
            or query.get("mid") != ["b20402000000"]
            or query.get("cate_no") != ["10"]
        ):
            raise TongyeongContractError("GNE detail link identity is malformed")
        identities.add(identity_values[0])
    if len(identities) != 1:
        raise TongyeongContractError(
            f"GNE page {requested_page} row has {len(identities)} identities"
        )
    identity = next(iter(identities))

    title_node = cells[1].select_one("a[href*='/usr_gne/lec_v.es'] span")
    if title_node is None:
        raise TongyeongContractError(f"GNE {identity} list title missing")
    title = _gne_badgeless_title(title_node.get_text(" ", strip=True))
    if not title:
        raise TongyeongContractError(f"GNE {identity} list title empty")
    title_cell_text = cells[1].get_text(" ", strip=True)
    apply_period = _gne_period_from_cell(
        title_cell_text, "모집기간", f"GNE {identity}"
    )
    period = _gne_period_from_cell(title_cell_text, "학습기간", f"GNE {identity}")
    schedule_match = re.search(
        r"교육요일/시간\s*:\s*(.+)$", _clean(title_cell_text)
    )
    if not schedule_match or not _clean(schedule_match.group(1)):
        raise TongyeongContractError(f"GNE {identity} schedule missing")
    schedule = _clean(schedule_match.group(1))
    apply_start, apply_end = _date_pair(apply_period, f"GNE {identity} application")
    start, end = _date_pair(period, f"GNE {identity} education")

    capacity_text = _clean(cells[3].get_text(" ", strip=True))
    capacity_match = re.fullmatch(
        r"모집\s*(\d{1,6})\s*/\s*(\d{1,6})\s+"
        r"온라인\s*(\d{1,6})\s*/\s*(\d{1,6})\s+"
        r"후보\s*(\d{1,6})\s*/\s*(\d{1,6})",
        capacity_text,
    )
    if not capacity_match:
        raise TongyeongContractError(f"GNE {identity} capacity is malformed")
    current, total, online_current, online_total, wait_current, wait_total = (
        int(value) for value in capacity_match.groups()
    )
    if (
        total < 1
        or online_total != total
        or online_current != current
        or wait_total < wait_current
    ):
        raise TongyeongContractError(f"GNE {identity} capacity is inconsistent")

    methods = tuple(
        dict.fromkeys(
            _clean(image.get("alt", ""))
            for image in cells[4].select("img[alt]")
            if _clean(image.get("alt", ""))
        )
    )
    if not methods:
        raise TongyeongContractError(f"GNE {identity} receipt method missing")
    statuses = {
        _clean(image.get("alt", ""))
        for image in cells[5].select("img[alt]")
        if _clean(image.get("alt", ""))
    }
    if len(statuses) != 1 or next(iter(statuses)) not in _GNE_STATUS_MAP:
        raise TongyeongContractError(f"GNE {identity} source status is unknown")
    source_status = next(iter(statuses))
    return {
        "identity": identity,
        "list_page": requested_page,
        "title": title,
        "source_category": _clean(cells[0].get_text(" ", strip=True)),
        "target": _clean(cells[2].get_text(" ", strip=True)),
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "period": period,
        "start": start,
        "end": end,
        "schedule": schedule,
        "capacity_text": capacity_text,
        "capacity_current": current,
        "capacity_total": total,
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "methods": methods,
        "source_status": source_status,
        "detail_url": tongyeong_gne_detail_url(identity),
    }


def _parse_gne_page(soup: BeautifulSoup, requested_page: int) -> _GnePage:
    table = _table_by_headers(soup, _GNE_LIST_HEADERS)
    body = table.select_one("tbody")
    if body is None:
        raise TongyeongContractError("GNE list tbody missing")
    rows: list[dict[str, Any]] = []
    structural_empty = False
    data_nodes = body.find_all("tr", recursive=False)
    for node in data_nodes:
        cells = node.find_all("td", recursive=False)
        if len(cells) == 1:
            text = _clean(cells[0].get_text(" ", strip=True))
            if text != "데이터가 없습니다.":
                raise TongyeongContractError(
                    f"GNE page {requested_page} unknown empty marker {text!r}"
                )
            structural_empty = True
            continue
        if structural_empty:
            raise TongyeongContractError(
                f"GNE page {requested_page} mixed sentinel and data"
            )
        rows.append(_gne_parse_list_row(node, requested_page))
    if not rows and not structural_empty:
        raise TongyeongContractError(
            f"GNE page {requested_page} was neither data nor exact empty sentinel"
        )
    return _GnePage(
        requested_page=requested_page,
        declared_last=_gne_declared_last(soup),
        rows=tuple(rows),
        structural_empty=structural_empty,
    )


def _gne_signature(page: _GnePage) -> tuple[Any, ...]:
    return (
        page.declared_last,
        page.structural_empty,
        tuple(
            (
                row["identity"],
                row["title"],
                row["source_status"],
                row["apply_period"],
                row["period"],
                row["capacity_text"],
            )
            for row in page.rows
        ),
    )


def _detail_pairs(
    table: Tag,
    safe_labels: frozenset[str],
    discarded_labels: frozenset[str],
    *,
    label_selector: str,
) -> dict[str, str]:
    allowed = safe_labels | discarded_labels
    result: dict[str, str] = {}
    for label_node in table.select(label_selector):
        label = _clean(label_node.get_text(" ", strip=True)).lstrip("*").strip()
        if not label:
            continue
        if label not in allowed:
            raise TongyeongContractError(f"unknown structured detail label {label!r}")
        value_node = label_node.find_next_sibling("td")
        if value_node is None:
            raise TongyeongContractError(f"detail label {label!r} has no value")
        if label in result:
            # The GNE template repeats '초빙강사' as a value and then as a
            # second label.  A duplicate structured label is safe only when
            # it belongs to the explicit discard set.
            if label not in discarded_labels:
                raise TongyeongContractError(f"duplicate detail label {label!r}")
            continue
        images = [
            _clean(image.get("alt", ""))
            for image in value_node.select("img[alt]")
            if _clean(image.get("alt", ""))
        ]
        result[label] = "|".join(images) or _clean(
            value_node.get_text(" ", strip=True)
        )
    missing = safe_labels - set(result)
    if missing:
        raise TongyeongContractError(
            "missing structured detail labels " + ", ".join(sorted(missing))
        )
    return result


def _gne_register_urls(soup: BeautifulSoup, identity: str) -> list[str]:
    candidates: list[str] = []
    pattern = re.compile(r"(?P<url>/usr_gne/register\.es\?[^\"'<>\s]+)")
    for match in pattern.finditer(html.unescape(str(soup))):
        raw = match.group("url")
        absolute = _absolute_same_source_url(
            raw,
            TONGYEONG_GNE_LIST_URL,
            TONGYEONG_GNE_HOST,
            TONGYEONG_GNE_REGISTER_PATH,
            f"GNE {identity} register URL",
        )
        query = parse_qs(urlparse(absolute).query, keep_blank_values=True)
        if (
            query.get("gno") == [identity]
            and query.get("mid") == ["b20402000000"]
            and query.get("cate_no") == ["10"]
        ):
            candidates.append(absolute)
    return list(dict.fromkeys(candidates))


def _gne_application_control(
    soup: BeautifulSoup,
    listed: Mapping[str, Any],
) -> tuple[bool, str, bool]:
    identity = _clean(listed.get("identity"))
    controls: list[str] = []
    for node in soup.select("a[onclick], button[onclick], input[onclick], a[href]"):
        action = _clean(node.get("onclick", "") or node.get("href", ""))
        if "requestStudent" not in action:
            continue
        match = _GNE_APPLY_RE.fullmatch(action)
        if not match:
            raise TongyeongContractError(
                f"GNE {identity} application control is malformed"
            )
        controls.append(match.group("id"))
    if any(item != identity for item in controls):
        raise TongyeongContractError(
            f"GNE {identity} application control identity mismatch"
        )
    register_urls = _gne_register_urls(soup, identity)
    source_status = _clean(listed.get("source_status"))
    private_forms = soup.select("form#childForm")
    private_form_present = bool(private_forms)
    for form in private_forms:
        absolute = urljoin(TONGYEONG_GNE_LIST_URL, _clean(form.get("action", "")))
        parsed = urlparse(absolute)
        if (
            not _safe_https(parsed, TONGYEONG_GNE_HOST)
            or parsed.path != TONGYEONG_GNE_PRIVATE_FORM_PATH
        ):
            raise TongyeongContractError(
                f"GNE {identity} private form escaped official action"
            )
    if source_status == "모집중":
        if controls != [identity] or len(register_urls) != 1 or not private_form_present:
            raise TongyeongContractError(
                f"GNE {identity} open application identity/control incomplete"
            )
        return True, register_urls[0], True
    if controls or private_form_present:
        raise TongyeongContractError(
            f"GNE {identity} inactive row exposes an active application control"
        )
    return False, "", False


def _parse_gne_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    detail_tables = [
        table
        for table in soup.select("table")
        if any(
            _clean(cell.get_text(" ", strip=True)).lstrip("*").strip() == "강좌명"
            for cell in table.select("td.boardText1")
        )
    ]
    if len(detail_tables) != 1:
        raise TongyeongContractError(
            f"GNE {identity} expected one detail table, got {len(detail_tables)}"
        )
    table = detail_tables[0]
    fields = _detail_pairs(
        table,
        _GNE_SAFE_DETAIL_LABELS - {"모집인원", "온라인접수", "후보"},
        _GNE_DISCARDED_DETAIL_LABELS,
        label_selector="td.boardText1",
    )
    # Three capacity labels use plain td elements rather than boardText1.
    all_cells = table.find_all("td")
    for label in ("모집인원", "온라인접수", "후보"):
        matches = [
            index
            for index, cell in enumerate(all_cells[:-1])
            if _clean(cell.get_text(" ", strip=True)) == label
        ]
        if len(matches) != 1:
            raise TongyeongContractError(f"GNE {identity} {label} label malformed")
        fields[label] = _clean(
            all_cells[matches[0] + 1].get_text(" ", strip=True)
        )

    if fields["강좌명"] != _clean(listed.get("title")):
        raise TongyeongContractError(f"GNE {identity} detail title mismatch")
    if fields["교육대상"] != _clean(listed.get("target")):
        raise TongyeongContractError(f"GNE {identity} detail target mismatch")
    apply_start, apply_end = _date_pair(
        fields["모집기간"], f"GNE {identity} detail application"
    )
    start, end = _date_pair(fields["교육기간"], f"GNE {identity} detail education")
    if (apply_start, apply_end) != (
        listed.get("apply_start"),
        listed.get("apply_end"),
    ):
        raise TongyeongContractError(f"GNE {identity} application period mismatch")
    if (start, end) != (listed.get("start"), listed.get("end")):
        raise TongyeongContractError(f"GNE {identity} education period mismatch")
    if _clean(fields["교육일시"]) != _clean(listed.get("schedule")):
        raise TongyeongContractError(f"GNE {identity} schedule mismatch")
    detail_statuses = tuple(
        item for item in fields["진행상태"].split("|") if _clean(item)
    )
    if detail_statuses != (_clean(listed.get("source_status")),):
        raise TongyeongContractError(f"GNE {identity} detail status mismatch")
    detail_methods = tuple(
        item for item in fields["접수방법"].split("|") if _clean(item)
    )
    if set(detail_methods) != set(listed.get("methods", ())):
        raise TongyeongContractError(f"GNE {identity} receipt methods mismatch")
    capacity_current = int(_clean(fields["모집인원"]).split("/")[0])
    capacity_total = int(_clean(fields["모집인원"]).split("/")[-1])
    online_current = int(_clean(fields["온라인접수"]).split("/")[0])
    online_total = int(_clean(fields["온라인접수"]).split("/")[-1])
    wait_current = int(_clean(fields["후보"]).split("/")[0])
    wait_total = int(_clean(fields["후보"]).split("/")[-1])
    expected_capacity = (
        listed.get("capacity_current"),
        listed.get("capacity_total"),
        listed.get("capacity_current"),
        listed.get("capacity_total"),
        listed.get("waitlist_current"),
        listed.get("waitlist_total"),
    )
    if (
        capacity_current,
        capacity_total,
        online_current,
        online_total,
        wait_current,
        wait_total,
    ) != expected_capacity:
        raise TongyeongContractError(f"GNE {identity} detail capacity mismatch")

    source_status = _clean(listed.get("source_status"))
    if source_status == "모집중" and not (apply_start <= cutoff <= apply_end):
        raise TongyeongContractError(f"GNE {identity} open status/date mismatch")
    if source_status == "준비중" and cutoff > apply_start:
        raise TongyeongContractError(f"GNE {identity} scheduled status/date mismatch")
    if source_status == "교육중" and not (start <= cutoff <= end):
        raise TongyeongContractError(f"GNE {identity} active status/date mismatch")
    control, application_url, private_form_present = _gne_application_control(
        soup, listed
    )
    venue = _clean(fields["강의장소"])
    if not venue:
        raise TongyeongContractError(f"GNE {identity} venue missing")
    fee = _clean(fields["수강료"])
    row: dict[str, Any] = {
        "provider": TONGYEONG_GNE_PROVIDER,
        "provider_course_id": f"{TONGYEONG_GNE_PROVIDER}:gno:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": fields["강좌명"],
        "description": fields["강좌명"],
        "branch": TONGYEONG_GNE_BRANCH,
        "branch_code": _branch_code("tongyeong-gne", TONGYEONG_GNE_BRANCH),
        "preserve_branch": True,
        "category": "도서관 문화강좌",
        "program_type": "교육",
        "raw_url": _clean(listed.get("detail_url")),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED" if control else "INFO_ONLY"
        ),
        "application_method": "온라인" if control else "",
        "application_methods": ["온라인"] if control else [],
        "reservation_available": control,
        "status": _GNE_STATUS_MAP[source_status],
        "fee": fee,
        "fee_amount": _fee_amount(fee),
        "material_fee": _clean(fields["재료비"]),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(fields["모집기간"]),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": _clean(fields["교육일시"]),
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_current": wait_current,
        "waitlist_total": wait_total,
        "target": fields["교육대상"],
        "venue": venue,
        "venue_name": venue,
        "address": TONGYEONG_GNE_ADDRESS,
        "venue_address": TONGYEONG_GNE_ADDRESS,
        "collection_category": "도서관",
        "domain_category": "교육·강좌",
        "operator_type": "교육청 공공도서관",
        "source_group": "library",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": TONGYEONG_GNE_PARSER,
        "municipality_code": TONGYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": TONGYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": listed.get("list_page"),
            "source_category": _clean(listed.get("source_category")),
            "source_status": source_status,
            "source_methods": list(listed.get("methods", ())),
            "source_capacity": _clean(listed.get("capacity_text")),
            "source_material_fee": _clean(fields["재료비"]),
            "detail_verified": True,
            "application_control_present": control,
            "application_control_identity": identity if control else "",
            "private_application_form_present_but_not_requested": (
                private_form_present
            ),
            "service_family": "education",
        },
        "_source_end_date": end,
    }
    return row


def collect_tongyeong_gne_library(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the complete GNE Tongyeong Library course ledger."""

    meta = _base_meta(
        "gne_education_office_library",
        TONGYEONG_GNE_PARSER,
        TONGYEONG_GNE_LIST_URL,
    )
    meta["canonical_candidate_id"] = TONGYEONG_GNE_CANDIDATE_ID
    if not is_tongyeong_gne_library_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact GNE Tongyeong Library owner"
        )
        return [], TONGYEONG_GNE_PARSER, meta
    if not _validate_limits(timeout, max_pages, detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], TONGYEONG_GNE_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], TONGYEONG_GNE_PARSER, meta

    current_factory = session_factory or _default_session_factory
    current_requester = requester or _default_requester
    session = current_factory()
    errors: list[str] = []
    result: list[dict[str, Any]] = []
    try:
        first = _parse_gne_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_gne_page_url(1),
                timeout,
            ),
            1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        declared_last = first.declared_last
        meta.update(
            {
                "declared_last_page": declared_last,
                "sentinel_page": declared_last + 1,
            }
        )
        if declared_last > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"max_pages cap allows {max_pages} of "
                        f"{declared_last} declared GNE pages"
                    ),
                }
            )
            return [], TONGYEONG_GNE_PARSER, meta
        pages: list[_GnePage] = [first]
        for page_number in range(2, declared_last + 1):
            page = _parse_gne_page(
                _request_soup(
                    session,
                    current_requester,
                    tongyeong_gne_page_url(page_number),
                    timeout,
                ),
                page_number,
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            pages.append(page)
        for page in pages:
            if page.structural_empty or page.declared_last != declared_last:
                errors.append(
                    f"GNE data page {page.requested_page} boundary mismatch"
                )

        sentinel = _parse_gne_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_gne_page_url(declared_last + 1),
                timeout,
            ),
            declared_last + 1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        if (
            not sentinel.structural_empty
            or sentinel.rows
            or sentinel.declared_last != declared_last
        ):
            errors.append("GNE post-last page was not the exact empty sentinel")

        boundary_pages = [pages[0]]
        if pages[-1].requested_page != pages[0].requested_page:
            boundary_pages.append(pages[-1])
        for original in boundary_pages:
            rechecked = _parse_gne_page(
                _request_soup(
                    session,
                    current_requester,
                    tongyeong_gne_page_url(original.requested_page),
                    timeout,
                ),
                original.requested_page,
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            meta["stability_rechecks"] += 1
            if _gne_signature(rechecked) != _gne_signature(original):
                errors.append(
                    f"GNE page {original.requested_page} stability recheck changed"
                )
        sentinel_recheck = _parse_gne_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_gne_page_url(declared_last + 1),
                timeout,
            ),
            declared_last + 1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        meta["stability_rechecks"] += 1
        if _gne_signature(sentinel_recheck) != _gne_signature(sentinel):
            errors.append("GNE post-last sentinel stability recheck changed")

        meta["required_list_requests"] = (
            declared_last + 1 + len(boundary_pages) + 1
        )
        listed = [row for page in pages for row in page.rows]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(f"{duplicate_count} duplicate GNE official identities")
        list_complete = bool(
            not errors
            and meta["list_requests"] == meta["required_list_requests"]
            and meta["stability_rechecks"] == len(boundary_pages) + 1
        )
        meta.update(
            {
                "data_pages": declared_last,
                "source_total": len(listed),
                "source_rows": len(listed),
                "source_status_counts": dict(
                    Counter(_clean(row.get("source_status")) for row in listed)
                ),
                "identity_duplicate_count": duplicate_count,
                "pagination_complete": list_complete,
            }
        )
        if not list_complete:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
            return [], TONGYEONG_GNE_PARSER, meta

        candidates = [row for row in listed if row["end"] >= cutoff]
        meta.update(
            {
                "current_candidate_count": len(candidates),
                "archived_rows_skipped_before_detail": len(listed)
                - len(candidates),
            }
        )
        if len(candidates) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"detail_limit cap allows {detail_limit} of "
                        f"{len(candidates)} current/future GNE details"
                    ),
                }
            )
            return [], TONGYEONG_GNE_PARSER, meta
        meta["detail_attempts"] = len(candidates)
        detailed: list[dict[str, Any]] = []
        for listed_row in candidates:
            identity = _clean(listed_row.get("identity"))
            try:
                soup = _request_soup(
                    session,
                    current_requester,
                    _clean(listed_row.get("detail_url")),
                    timeout,
                    {"Referer": TONGYEONG_GNE_LIST_URL},
                )
                detailed.append(_parse_gne_detail(listed_row, soup, cutoff))
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"GNE detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
                meta["detail_errors"] += 1
        details_complete = bool(
            not errors
            and meta["detail_attempts"] == meta["detail_pages"]
            and len(detailed) == len(candidates)
        )
        controls_complete = bool(
            details_complete
            and all(
                row.get("raw_fields", {}).get("detail_verified")
                for row in detailed
            )
        )
        if details_complete and controls_complete:
            result = _finalize_rows(
                detailed, _GNE_SAFE_RAW_FIELDS, dedupe_rows, errors
            )
        snapshot_complete = bool(
            list_complete and details_complete and controls_complete and not errors
        )
        if not snapshot_complete:
            result = []
        meta.update(
            {
                "current_source_count": len(detailed),
                "expired_count": len(listed) - len(detailed),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "application_control_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "application_control_present"
                        )
                    )
                    for row in detailed
                ),
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not detailed),
                "no_current_reason": (
                    "all GNE Tongyeong Library courses are expired"
                    if snapshot_complete and not detailed
                    else ""
                ),
                "municipality_coverage": [TONGYEONG_MUNICIPALITY_CODE],
                "discovery_audit": dict(TONGYEONG_DISCOVERY_AUDIT),
                "owner_boundary_audit": {
                    key: dict(value)
                    for key, value in TONGYEONG_OWNER_BOUNDARY_AUDIT.items()
                },
                "pii_fields_never_persisted": list(
                    TONGYEONG_PII_FIELDS_NEVER_PERSISTED
                ),
                "configured_collection_error": "; ".join(
                    dict.fromkeys(errors)
                ),
            }
        )
        return result, TONGYEONG_GNE_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: {_clean(exc)}"
        )
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], TONGYEONG_GNE_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def _city_application_control(
    status_cell: Tag,
    identity: str,
    source_status: str,
) -> tuple[bool, str]:
    status = _CITY_STATUS_MAP.get(source_status)
    active_urls: list[str] = []
    for node in status_cell.select("a[href], button[data-href]"):
        raw = _clean(node.get("href", "") or node.get("data-href", ""))
        if not raw or raw == "#" or raw.casefold().startswith("javascript:"):
            continue
        absolute = _absolute_same_source_url(
            raw,
            TONGYEONG_CITY_LIST_URL,
            TONGYEONG_CITY_HOST,
            TONGYEONG_CITY_LIST_PATH,
            f"city lifelong {identity} application URL",
        )
        query = parse_qs(urlparse(absolute).query, keep_blank_values=True)
        if query.get("idx") != [identity]:
            raise TongyeongContractError(
                f"city lifelong {identity} application identity mismatch"
            )
        if query.get("amode") in (None, [""], ["view"]):
            raise TongyeongContractError(
                f"city lifelong {identity} application action missing"
            )
        active_urls.append(absolute)
    active_urls = list(dict.fromkeys(active_urls))
    if status == "OPEN":
        if len(active_urls) != 1:
            raise TongyeongContractError(
                f"city lifelong {identity} open control count is "
                f"{len(active_urls)}"
            )
        return True, active_urls[0]
    if active_urls:
        raise TongyeongContractError(
            f"city lifelong {identity} inactive row exposes application control"
        )
    return False, ""


def _city_parse_list_row(row: Tag, requested_page: int) -> dict[str, Any]:
    cells = row.find_all(["th", "td"], recursive=False)
    if len(cells) != 8:
        raise TongyeongContractError(
            f"city lifelong page {requested_page} row requires eight cells"
        )
    anchor = cells[1].select_one("a[href*='idx=']")
    if anchor is None:
        raise TongyeongContractError(
            f"city lifelong page {requested_page} detail link missing"
        )
    absolute = _absolute_same_source_url(
        anchor.get("href", ""),
        TONGYEONG_CITY_LIST_URL,
        TONGYEONG_CITY_HOST,
        TONGYEONG_CITY_LIST_PATH,
        "city lifelong detail link",
    )
    query = parse_qs(urlparse(absolute).query, keep_blank_values=True)
    identities = query.get("idx", [])
    if (
        len(identities) != 1
        or not _LIBRARY_ID_RE.fullmatch(identities[0])
        or query.get("amode") != ["view"]
    ):
        raise TongyeongContractError("city lifelong detail identity malformed")
    identity = identities[0]
    institution_text = _clean(cells[0].get_text(" ", strip=True))
    institution_match = re.fullmatch(r"\[([^\[\]]+)\]", institution_text)
    if not institution_match:
        raise TongyeongContractError(
            f"city lifelong {identity} institution label malformed"
        )
    institution = _clean(institution_match.group(1))
    title = _clean(anchor.get_text(" ", strip=True))
    if not title or not institution:
        raise TongyeongContractError(
            f"city lifelong {identity} title/institution missing"
        )
    capacity_match = re.fullmatch(
        r"(\d{1,6})\s*/\s*(\d{1,6})\s*명",
        _clean(cells[2].get_text(" ", strip=True)),
    )
    if not capacity_match:
        raise TongyeongContractError(
            f"city lifelong {identity} capacity malformed"
        )
    capacity_total, waitlist_total = (
        int(value) for value in capacity_match.groups()
    )
    apply_period = _clean(cells[3].get_text(" ", strip=True))
    period = _clean(cells[4].get_text(" ", strip=True))
    apply_start, apply_end = _date_pair(
        apply_period, f"city lifelong {identity} application"
    )
    start, end = _date_span(period, f"city lifelong {identity} education")
    method = _clean(cells[5].get_text(" ", strip=True))
    fee = _clean(cells[6].get_text(" ", strip=True))
    if not method or not fee:
        raise TongyeongContractError(
            f"city lifelong {identity} method/fee missing"
        )
    source_status = _clean(cells[7].get_text(" ", strip=True))
    if source_status and source_status not in _CITY_STATUS_MAP:
        raise TongyeongContractError(
            f"city lifelong {identity} source status unknown"
        )
    control, application_url = _city_application_control(
        cells[7], identity, source_status
    )
    return {
        "identity": identity,
        "list_page": requested_page,
        "institution": institution,
        "title": title,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "period": period,
        "start": start,
        "end": end,
        "method": method,
        "fee": fee,
        "source_status": source_status,
        "application_control": control,
        "application_url": application_url,
        "detail_url": tongyeong_city_detail_url(identity),
    }


def _parse_city_page(soup: BeautifulSoup, requested_page: int) -> _CityPage:
    text = _clean(soup.get_text(" ", strip=True))
    summaries = _CITY_TOTAL_RE.findall(text)
    if len(summaries) != 1:
        raise TongyeongContractError(
            f"city lifelong page {requested_page} declared summary count is "
            f"{len(summaries)}"
        )
    total_text, current_text, last_text = summaries[0]
    declared_total = int(total_text.replace(",", ""))
    reported_page = int(current_text)
    declared_last = int(last_text)
    if declared_total < 0 or reported_page < 1 or declared_last < 1:
        raise TongyeongContractError("city lifelong declared summary invalid")
    expected_last = max(1, math.ceil(declared_total / TONGYEONG_CITY_PAGE_SIZE))
    if declared_last != expected_last or reported_page > declared_last:
        raise TongyeongContractError(
            "city lifelong declared total/page arithmetic mismatch"
        )
    table = _table_by_headers(soup, _CITY_LIST_HEADERS)
    body = table.select_one("tbody")
    if body is None:
        raise TongyeongContractError("city lifelong tbody missing")
    rows = tuple(
        _city_parse_list_row(row, requested_page)
        for row in body.find_all("tr", recursive=False)
    )
    return _CityPage(
        requested_page=requested_page,
        reported_page=reported_page,
        declared_last=declared_last,
        declared_total=declared_total,
        rows=rows,
    )


def _city_signature(page: _CityPage) -> tuple[Any, ...]:
    return (
        page.reported_page,
        page.declared_last,
        page.declared_total,
        tuple(
            (
                row["identity"],
                row["institution"],
                row["title"],
                row["apply_period"],
                row["period"],
                row["source_status"],
            )
            for row in page.rows
        ),
    )


def _parse_city_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    titles = [
        node
        for node in soup.select("h1.h1")
        if _clean(node.get_text(" ", strip=True)).startswith("[")
    ]
    if len(titles) != 1:
        raise TongyeongContractError(
            f"city lifelong {identity} detail title count is {len(titles)}"
        )
    expected_heading = (
        f"[{_clean(listed.get('institution'))}] {_clean(listed.get('title'))}"
    )
    if _clean(titles[0].get_text(" ", strip=True)) != expected_heading:
        raise TongyeongContractError(
            f"city lifelong {identity} detail title/institution mismatch"
        )
    detail_tables = [
        table
        for table in soup.select("table")
        if table.select_one("th")
        and "교육기간" in {
            _clean(node.get_text(" ", strip=True)) for node in table.select("th")
        }
    ]
    if len(detail_tables) != 1:
        raise TongyeongContractError(
            f"city lifelong {identity} detail table count is {len(detail_tables)}"
        )
    # Optional fields are not present on every official detail, so parse them
    # separately while retaining the unknown-label guard.
    present_labels = {
        _clean(node.get_text(" ", strip=True))
        for node in detail_tables[0].select("th")
    }
    missing_required = _CITY_REQUIRED_DETAIL_LABELS - present_labels
    if missing_required:
        raise TongyeongContractError(
            f"city lifelong {identity} required labels missing: "
            + ", ".join(sorted(missing_required))
        )
    allowed = (
        _CITY_REQUIRED_DETAIL_LABELS
        | _CITY_OPTIONAL_DETAIL_LABELS
        | _CITY_DISCARDED_DETAIL_LABELS
    )
    fields: dict[str, str] = {}
    for label_node in detail_tables[0].select("th"):
        label = _clean(label_node.get_text(" ", strip=True))
        if label not in allowed:
            raise TongyeongContractError(
                f"city lifelong {identity} unknown detail label {label!r}"
            )
        value_node = label_node.find_next_sibling("td")
        if value_node is None or label in fields:
            raise TongyeongContractError(
                f"city lifelong {identity} detail label {label!r} malformed"
            )
        fields[label] = _clean(value_node.get_text(" ", strip=True))

    apply_start, apply_end = _date_pair(
        fields["접수기간"], f"city lifelong {identity} detail application"
    )
    start, end = _date_span(
        fields["교육기간"], f"city lifelong {identity} detail education"
    )
    if (apply_start, apply_end) != (
        listed.get("apply_start"),
        listed.get("apply_end"),
    ):
        raise TongyeongContractError(
            f"city lifelong {identity} application period mismatch"
        )
    if (start, end) != (listed.get("start"), listed.get("end")):
        raise TongyeongContractError(
            f"city lifelong {identity} education period mismatch"
        )
    if _clean(fields["수강료"]) != _clean(listed.get("fee")):
        raise TongyeongContractError(f"city lifelong {identity} fee mismatch")
    if _clean(fields["접수방법"]) != _clean(listed.get("method")):
        raise TongyeongContractError(
            f"city lifelong {identity} application method mismatch"
        )
    source_status = _clean(listed.get("source_status"))
    if source_status not in _CITY_STATUS_MAP:
        raise TongyeongContractError(
            f"city lifelong {identity} current row has no source status"
        )
    status = _CITY_STATUS_MAP[source_status]
    if status == "OPEN" and not (apply_start <= cutoff <= apply_end):
        raise TongyeongContractError(
            f"city lifelong {identity} open status/date mismatch"
        )
    if status == "SCHEDULED" and cutoff > apply_start:
        raise TongyeongContractError(
            f"city lifelong {identity} scheduled status/date mismatch"
        )
    control = bool(listed.get("application_control"))
    application_url = _clean(listed.get("application_url")) if control else ""
    branch = _clean(listed.get("institution"))
    venue = _clean(fields.get("교육장소")) or branch
    fee = _clean(fields["수강료"])
    method = _clean(fields["접수방법"])
    online = "온라인" in method
    row: dict[str, Any] = {
        "provider": TONGYEONG_CITY_PROVIDER,
        "provider_course_id": f"{TONGYEONG_CITY_PROVIDER}:idx:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "description": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": _branch_code("tongyeong-city", branch),
        "preserve_branch": True,
        "category": "평생학습",
        "program_type": "교육",
        "raw_url": _clean(listed.get("detail_url")),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED"
            if control and online
            else "INFO_ONLY"
        ),
        "application_method": method,
        "application_methods": [method] if method else [],
        "reservation_available": control,
        "status": status,
        "fee": fee,
        "fee_amount": _fee_amount(fee),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": _clean(fields["접수기간"]),
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": _clean(fields.get("교육시간")),
        "capacity": f"{int(listed.get('capacity_total', 0))}명",
        "capacity_total": int(listed.get("capacity_total", 0)),
        "waitlist_total": int(listed.get("waitlist_total", 0)),
        "target": _clean(fields["모집대상"]),
        "venue": venue,
        "venue_name": venue,
        "address": "",
        "venue_address": "",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "lifelong_learning",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": TONGYEONG_CITY_PARSER,
        "municipality_code": TONGYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": TONGYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "list_page": listed.get("list_page"),
            "source_institution": branch,
            "source_status": source_status,
            "source_application_method": method,
            "detail_verified": True,
            "application_control_present": control,
            "application_control_identity": identity if control else "",
            "service_family": "education",
        },
        "_source_end_date": end,
    }
    return row


def collect_tongyeong_city_lifelong(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect Tongyeong City's complete lifelong-learning ledger."""

    meta = _base_meta(
        "city_lifelong_learning",
        TONGYEONG_CITY_PARSER,
        TONGYEONG_CITY_LIST_URL,
    )
    meta["canonical_candidate_id"] = TONGYEONG_CITY_CANDIDATE_ID
    if not is_tongyeong_city_lifelong_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact Tongyeong City lifelong owner"
        )
        return [], TONGYEONG_CITY_PARSER, meta
    if not _validate_limits(timeout, max_pages, detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], TONGYEONG_CITY_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], TONGYEONG_CITY_PARSER, meta

    current_factory = session_factory or _default_session_factory
    current_requester = requester or _default_requester
    session = current_factory()
    errors: list[str] = []
    result: list[dict[str, Any]] = []
    try:
        first = _parse_city_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_city_page_url(1),
                timeout,
            ),
            1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        declared_last = first.declared_last
        declared_total = first.declared_total
        meta.update(
            {
                "declared_last_page": declared_last,
                "declared_total": declared_total,
                "sentinel_page": declared_last + 1,
                "sentinel_mode": "exact_last_page_clamp",
            }
        )
        if declared_last > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"max_pages cap allows {max_pages} of "
                        f"{declared_last} declared city lifelong pages"
                    ),
                }
            )
            return [], TONGYEONG_CITY_PARSER, meta
        pages: list[_CityPage] = [first]
        for page_number in range(2, declared_last + 1):
            page = _parse_city_page(
                _request_soup(
                    session,
                    current_requester,
                    tongyeong_city_page_url(page_number),
                    timeout,
                ),
                page_number,
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            pages.append(page)
        for page in pages:
            if (
                page.reported_page != page.requested_page
                or page.declared_last != declared_last
                or page.declared_total != declared_total
            ):
                errors.append(
                    f"city lifelong page {page.requested_page} boundary mismatch"
                )
        expected_last_rows = declared_total % TONGYEONG_CITY_PAGE_SIZE
        if expected_last_rows == 0 and declared_total:
            expected_last_rows = TONGYEONG_CITY_PAGE_SIZE
        if declared_total == 0:
            expected_last_rows = 0
        for page in pages[:-1]:
            if len(page.rows) != TONGYEONG_CITY_PAGE_SIZE:
                errors.append(
                    f"city lifelong page {page.requested_page} is not a full page"
                )
        if len(pages[-1].rows) != expected_last_rows:
            errors.append(
                "city lifelong last-page cardinality disagrees with declared total"
            )

        sentinel = _parse_city_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_city_page_url(declared_last + 1),
                timeout,
            ),
            declared_last + 1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        if _city_signature(sentinel) != _city_signature(pages[-1]):
            errors.append(
                "city lifelong post-last page was not an exact last-page clamp"
            )

        boundary_pages = [pages[0]]
        if pages[-1].reported_page != pages[0].reported_page:
            boundary_pages.append(pages[-1])
        for original in boundary_pages:
            rechecked = _parse_city_page(
                _request_soup(
                    session,
                    current_requester,
                    tongyeong_city_page_url(original.reported_page),
                    timeout,
                ),
                original.reported_page,
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            meta["stability_rechecks"] += 1
            if _city_signature(rechecked) != _city_signature(original):
                errors.append(
                    f"city lifelong page {original.reported_page} "
                    "stability recheck changed"
                )
        meta["required_list_requests"] = declared_last + 1 + len(boundary_pages)

        listed = [row for page in pages for row in page.rows]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if len(listed) != declared_total:
            errors.append(
                f"city lifelong walked {len(listed)} of {declared_total} declared rows"
            )
        if duplicate_count:
            errors.append(
                f"{duplicate_count} duplicate city lifelong official identities"
            )
        list_complete = bool(
            not errors
            and meta["list_requests"] == meta["required_list_requests"]
            and meta["stability_rechecks"] == len(boundary_pages)
        )
        meta.update(
            {
                "data_pages": declared_last,
                "source_total": len(listed),
                "source_rows": len(listed),
                "source_institution_counts": dict(
                    Counter(_clean(row.get("institution")) for row in listed)
                ),
                "source_status_counts": dict(
                    Counter(
                        _clean(row.get("source_status")) or "NO_VISIBLE_STATUS"
                        for row in listed
                    )
                ),
                "identity_duplicate_count": duplicate_count,
                "pagination_complete": list_complete,
            }
        )
        if not list_complete:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
            return [], TONGYEONG_CITY_PARSER, meta

        candidates = [row for row in listed if row["end"] >= cutoff]
        meta.update(
            {
                "current_candidate_count": len(candidates),
                "archived_rows_skipped_before_detail": len(listed)
                - len(candidates),
            }
        )
        missing_current_statuses = [
            _clean(row.get("identity"))
            for row in candidates
            if _clean(row.get("source_status")) not in _CITY_STATUS_MAP
        ]
        if missing_current_statuses:
            meta["configured_collection_error"] = (
                "current/future city lifelong rows have no recognized status: "
                + ", ".join(missing_current_statuses)
            )
            return [], TONGYEONG_CITY_PARSER, meta
        if len(candidates) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"detail_limit cap allows {detail_limit} of "
                        f"{len(candidates)} current/future city details"
                    ),
                }
            )
            return [], TONGYEONG_CITY_PARSER, meta
        meta["detail_attempts"] = len(candidates)
        detailed: list[dict[str, Any]] = []
        for listed_row in candidates:
            identity = _clean(listed_row.get("identity"))
            try:
                soup = _request_soup(
                    session,
                    current_requester,
                    _clean(listed_row.get("detail_url")),
                    timeout,
                    {
                        "Referer": tongyeong_city_page_url(
                            int(listed_row.get("list_page", 1))
                        )
                    },
                )
                detailed.append(_parse_city_detail(listed_row, soup, cutoff))
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    f"city detail {identity}: {type(exc).__name__}: {_clean(exc)}"
                )
                meta["detail_errors"] += 1
        details_complete = bool(
            not errors
            and meta["detail_attempts"] == meta["detail_pages"]
            and len(detailed) == len(candidates)
        )
        controls_complete = bool(
            details_complete
            and all(
                row.get("raw_fields", {}).get("detail_verified")
                for row in detailed
            )
        )
        if details_complete and controls_complete:
            result = _finalize_rows(
                detailed, _CITY_SAFE_RAW_FIELDS, dedupe_rows, errors
            )
        snapshot_complete = bool(
            list_complete and details_complete and controls_complete and not errors
        )
        if not snapshot_complete:
            result = []
        meta.update(
            {
                "current_source_count": len(detailed),
                "expired_count": len(listed) - len(detailed),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "application_control_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "application_control_present"
                        )
                    )
                    for row in detailed
                ),
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not detailed),
                "no_current_reason": (
                    "all Tongyeong City lifelong courses are expired"
                    if snapshot_complete and not detailed
                    else ""
                ),
                "municipality_coverage": [TONGYEONG_MUNICIPALITY_CODE],
                "discovery_audit": dict(TONGYEONG_DISCOVERY_AUDIT),
                "owner_boundary_audit": {
                    key: dict(value)
                    for key, value in TONGYEONG_OWNER_BOUNDARY_AUDIT.items()
                },
                "pii_fields_never_persisted": list(
                    TONGYEONG_PII_FIELDS_NEVER_PERSISTED
                ),
                "configured_collection_error": "; ".join(
                    dict.fromkeys(errors)
                ),
            }
        )
        return result, TONGYEONG_CITY_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: {_clean(exc)}"
        )
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], TONGYEONG_CITY_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


_LIBRARY_LG_BROAD_BRANCH: Mapping[str, str] = {
    "2": "꿈이랑",
    "3": "충무",
    "7": "작은",
    "8": "시립",
    "9": "꿈이랑",
    "10": "시립",
    "14": "시립",
    "16": "시립",
}
_LIBRARY_FIXED_BRANCHES: Mapping[str, str] = {
    "시립": "통영시립도서관",
    "충무": "통영시립충무도서관",
    "꿈이랑": "꿈이랑도서관",
}
_LIBRARY_LIST_PERIOD_RE = re.compile(
    r"^(?P<apply_start>20\d{2}\.\d{1,2}\.\d{1,2}"
    r"(?:\s+\d{1,2}:\d{2})?)\s*~\s*"
    r"(?P<apply_end>20\d{2}\.\d{1,2}\.\d{1,2}"
    r"(?:\s+\d{1,2}:\d{2})?)\s+"
    r"(?P<start>20\d{2}\.\d{1,2}\.\d{1,2})\s*~\s*"
    r"(?P<end>20\d{2}\.\d{1,2}\.\d{1,2})\s*"
    r"(?P<schedule>.*)$"
)


def _library_pager(soup: BeautifulSoup) -> tuple[Optional[int], int]:
    pagers = soup.select("div.paging")
    if len(pagers) != 1:
        raise TongyeongContractError(
            f"municipal library pager count is {len(pagers)}"
        )
    pager = pagers[0]
    current_nodes = pager.find_all("strong", recursive=False)
    if len(current_nodes) > 1:
        raise TongyeongContractError("municipal library pager has multiple currents")
    reported_page: Optional[int] = None
    if current_nodes:
        current_text = _clean(current_nodes[0].get_text(" ", strip=True))
        if not current_text.isdigit() or int(current_text) < 1:
            raise TongyeongContractError(
                "municipal library current page marker malformed"
            )
        reported_page = int(current_text)
    pages: list[int] = [reported_page] if reported_page is not None else []
    for anchor in pager.select("a[href]"):
        absolute = _absolute_same_source_url(
            anchor.get("href", ""),
            TONGYEONG_LIBRARY_LIST_URL,
            TONGYEONG_LIBRARY_HOST,
            TONGYEONG_LIBRARY_PATH,
            "municipal library pager URL",
        )
        raw_page = parse_qs(urlparse(absolute).query).get("page", [])
        if len(raw_page) == 1 and raw_page[0].isdigit():
            pages.append(int(raw_page[0]))
    if not pages or min(pages) < 1:
        raise TongyeongContractError(
            "municipal library pager did not declare a last page"
        )
    return reported_page, max(pages)


def _library_identity_url(
    href: str,
    *,
    context: str,
    required_action: str,
    expected_lg: Optional[str] = None,
    expected_le: Optional[str] = None,
) -> tuple[str, str, str]:
    absolute = _absolute_same_source_url(
        href,
        TONGYEONG_LIBRARY_LIST_URL,
        TONGYEONG_LIBRARY_HOST,
        TONGYEONG_LIBRARY_PATH,
        context,
    )
    query = parse_qs(urlparse(absolute).query, keep_blank_values=True)
    lg_values = query.get("lgCode", [])
    le_values = query.get("leCode", [])
    if (
        query.get("g_page") != ["culture"]
        or query.get("m_page") != ["culture02"]
        or query.get("act") != [required_action]
        or len(lg_values) != 1
        or len(le_values) != 1
        or not _LIBRARY_ID_RE.fullmatch(lg_values[0])
        or not _LIBRARY_ID_RE.fullmatch(le_values[0])
    ):
        raise TongyeongContractError(f"{context} identity/action malformed")
    if expected_lg is not None and lg_values[0] != expected_lg:
        raise TongyeongContractError(f"{context} lgCode identity mismatch")
    if expected_le is not None and le_values[0] != expected_le:
        raise TongyeongContractError(f"{context} leCode identity mismatch")
    return absolute, lg_values[0], le_values[0]


def _library_parse_list_row(row: Tag, requested_page: int) -> dict[str, Any]:
    cells = row.find_all("td", recursive=False)
    if len(cells) != 5:
        raise TongyeongContractError(
            f"municipal library page {requested_page} row requires five cells"
        )
    anchor = cells[1].select_one("a[href*='act=lecture_view']")
    if anchor is None:
        raise TongyeongContractError(
            f"municipal library page {requested_page} detail link missing"
        )
    detail_url, lg_code, le_code = _library_identity_url(
        anchor.get("href", ""),
        context="municipal library detail URL",
        required_action="lecture_view",
    )
    broad_branch = _clean(cells[0].get_text(" ", strip=True))
    expected_broad = _LIBRARY_LG_BROAD_BRANCH.get(lg_code)
    if expected_broad is None or broad_branch != expected_broad:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) branch/lgCode mismatch"
        )
    title = _clean(anchor.get_text(" ", strip=True))
    target_node = cells[1].select_one("strong.blue, .blue")
    target = _clean(target_node.get_text(" ", strip=True)) if target_node else ""
    if not title:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) title missing"
        )
    capacity_text = _clean(cells[2].get_text(" ", strip=True))
    capacity_match = re.fullmatch(
        r"(\d{1,6})명\s*모집\s*(\d{1,6})명\s*신청\s*등록확인",
        capacity_text,
    )
    capacity_total: Optional[int] = None
    capacity_current: Optional[int] = None
    if capacity_match:
        capacity_total, capacity_current = (
            int(value) for value in capacity_match.groups()
        )
        if capacity_total < 1:
            capacity_total = None
            capacity_current = None

    private_links = cells[2].select(
        f"a[href*='act={TONGYEONG_LIBRARY_PRIVATE_CHECK_ACTION}']"
    )
    if len(private_links) > 1:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) private check identity "
            f"marker count is {len(private_links)}"
        )
    if private_links:
        _library_identity_url(
            private_links[0].get("href", ""),
            context="municipal library private registration-check URL",
            required_action=TONGYEONG_LIBRARY_PRIVATE_CHECK_ACTION,
            expected_lg=lg_code,
            expected_le=le_code,
        )

    period_text = _clean(cells[3].get_text(" ", strip=True))
    period_match = _LIBRARY_LIST_PERIOD_RE.fullmatch(period_text)
    if not period_match:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) periods malformed"
        )
    apply_period = (
        f"{period_match.group('apply_start')} ~ "
        f"{period_match.group('apply_end')}"
    )
    period = f"{period_match.group('start')} ~ {period_match.group('end')}"
    schedule = _clean(period_match.group("schedule"))
    apply_start, apply_end = _date_pair(
        apply_period, f"municipal library ({lg_code},{le_code}) application"
    )
    source_start = _date_token(
        period_match.group("start"),
        f"municipal library ({lg_code},{le_code}) education start",
    )
    source_end = _date_token(
        period_match.group("end"),
        f"municipal library ({lg_code},{le_code}) education end",
    )
    chronology_valid = source_start <= source_end
    start, end = min(source_start, source_end), max(source_start, source_end)
    statuses = {
        _clean(image.get("alt", ""))
        for image in cells[4].select("img[alt]")
        if _clean(image.get("alt", ""))
    }
    if len(statuses) != 1 or next(iter(statuses)) not in _LIBRARY_STATUS_MAP:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) source status unknown"
        )
    source_status = next(iter(statuses))
    receive_links = cells[4].select(
        f"a[href*='act={TONGYEONG_LIBRARY_RECEIVE_ACTION}']"
    )
    application_url = ""
    if _LIBRARY_STATUS_MAP[source_status] == "OPEN":
        if len(receive_links) != 1:
            raise TongyeongContractError(
                f"municipal library ({lg_code},{le_code}) open receive "
                f"control count is {len(receive_links)}"
            )
        application_url, _, _ = _library_identity_url(
            receive_links[0].get("href", ""),
            context="municipal library receive URL",
            required_action=TONGYEONG_LIBRARY_RECEIVE_ACTION,
            expected_lg=lg_code,
            expected_le=le_code,
        )
    elif receive_links:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) inactive receive control"
        )
    return {
        "identity": f"{lg_code}:{le_code}",
        "lg_code": lg_code,
        "le_code": le_code,
        "list_page": requested_page,
        "broad_branch": broad_branch,
        "title": title,
        "target": target,
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "apply_period": apply_period,
        "apply_start": apply_start,
        "apply_end": apply_end,
        "period": period,
        "start": start,
        "end": end,
        "source_start": source_start,
        "source_end": source_end,
        "chronology_valid": chronology_valid,
        "schedule": schedule,
        "source_status": source_status,
        "application_control": bool(application_url),
        "application_url": application_url,
        "private_check_verified": bool(private_links),
        "detail_url": detail_url,
    }


def _parse_library_page(
    soup: BeautifulSoup, requested_page: int
) -> _LibraryPage:
    reported_page, declared_last = _library_pager(soup)
    table = _table_by_headers(soup, _LIBRARY_LIST_HEADERS)
    body = table.select_one("tbody")
    if body is None:
        raise TongyeongContractError("municipal library tbody missing")
    row_nodes = body.find_all("tr", recursive=False)
    structural_empty = not row_nodes
    rows = tuple(
        _library_parse_list_row(row, requested_page) for row in row_nodes
    )
    if structural_empty and body.get_text(" ", strip=True):
        raise TongyeongContractError(
            "municipal library empty tbody contains unknown content"
        )
    return _LibraryPage(
        requested_page=requested_page,
        reported_page=reported_page,
        declared_last=declared_last,
        rows=rows,
        structural_empty=structural_empty,
    )


def _library_signature(page: _LibraryPage) -> tuple[Any, ...]:
    return (
        page.reported_page,
        page.declared_last,
        page.structural_empty,
        tuple(
            (
                row["identity"],
                row["broad_branch"],
                row["title"],
                row["apply_period"],
                row["period"],
                row["source_status"],
            )
            for row in page.rows
        ),
    )


def _library_detail_fields(box: Tag, identity: str) -> dict[str, str]:
    allowed = _LIBRARY_SAFE_DETAIL_LABELS | _LIBRARY_DISCARDED_DETAIL_LABELS
    fields: dict[str, str] = {}
    for label_node in box.select("dt"):
        label = _clean(label_node.get_text(" ", strip=True))
        if label not in allowed:
            raise TongyeongContractError(
                f"municipal library {identity} unknown detail label {label!r}"
            )
        value_node = label_node.find_next_sibling("dd")
        if value_node is None or label in fields:
            raise TongyeongContractError(
                f"municipal library {identity} detail label {label!r} malformed"
            )
        fields[label] = _clean(value_node.get_text(" ", strip=True))
    missing = (_LIBRARY_SAFE_DETAIL_LABELS | _LIBRARY_DISCARDED_DETAIL_LABELS) - set(
        fields
    )
    if missing:
        raise TongyeongContractError(
            f"municipal library {identity} missing detail labels: "
            + ", ".join(sorted(missing))
        )
    return fields


def _library_detail_receive_control(
    box: Tag,
    listed: Mapping[str, Any],
) -> tuple[bool, str]:
    lg_code = _clean(listed.get("lg_code"))
    le_code = _clean(listed.get("le_code"))
    source_status = _clean(listed.get("source_status"))
    receive_links = box.select(
        f".commend a[href*='act={TONGYEONG_LIBRARY_RECEIVE_ACTION}']"
    )
    if _LIBRARY_STATUS_MAP[source_status] == "OPEN":
        if len(receive_links) != 1:
            raise TongyeongContractError(
                f"municipal library ({lg_code},{le_code}) detail receive "
                f"count is {len(receive_links)}"
            )
        absolute, _, _ = _library_identity_url(
            receive_links[0].get("href", ""),
            context="municipal library detail receive URL",
            required_action=TONGYEONG_LIBRARY_RECEIVE_ACTION,
            expected_lg=lg_code,
            expected_le=le_code,
        )
        labels = [
            _clean(image.get("alt", ""))
            for image in receive_links[0].select("img[alt]")
            if _clean(image.get("alt", ""))
        ]
        if labels != [source_status]:
            raise TongyeongContractError(
                f"municipal library ({lg_code},{le_code}) detail receive label mismatch"
            )
        if urlparse(absolute).query != urlparse(
            _clean(listed.get("application_url"))
        ).query:
            raise TongyeongContractError(
                f"municipal library ({lg_code},{le_code}) list/detail receive mismatch"
            )
        return True, absolute
    if receive_links:
        raise TongyeongContractError(
            f"municipal library ({lg_code},{le_code}) closed detail has receive control"
        )
    return False, ""


def _library_exact_branch(
    broad_branch: str, venue: str, identity: str
) -> str:
    if broad_branch in _LIBRARY_FIXED_BRANCHES:
        return _LIBRARY_FIXED_BRANCHES[broad_branch]
    if broad_branch != "작은":
        raise TongyeongContractError(
            f"municipal library {identity} unknown branch tab {broad_branch!r}"
        )
    match = re.match(r"^(.+?작은도서관)(?:\s|$)", venue)
    if not match:
        raise TongyeongContractError(
            f"municipal library {identity} small-library venue cannot name branch"
        )
    return _clean(match.group(1))


def _parse_library_detail(
    listed: Mapping[str, Any], soup: BeautifulSoup, cutoff: date
) -> dict[str, Any]:
    identity = _clean(listed.get("identity"))
    boxes = soup.select("div.lecture_view")
    if len(boxes) != 1:
        raise TongyeongContractError(
            f"municipal library {identity} detail box count is {len(boxes)}"
        )
    box = boxes[0]
    title_node = box.select_one("h4")
    if title_node is None or _clean(title_node.get_text(" ", strip=True)) != _clean(
        listed.get("title")
    ):
        raise TongyeongContractError(
            f"municipal library {identity} detail title mismatch"
        )
    fields = _library_detail_fields(box, identity)
    if fields["대상"] != _clean(listed.get("target")):
        raise TongyeongContractError(
            f"municipal library {identity} detail target mismatch"
        )
    integer_fields: dict[str, int] = {}
    for label in ("인터넷 모집인원", "현재신청자수", "대기자 모집인원"):
        match = re.fullmatch(r"(\d{1,6})명", fields[label])
        if not match:
            raise TongyeongContractError(
                f"municipal library {identity} {label} malformed"
            )
        integer_fields[label] = int(match.group(1))
    if (
        integer_fields["인터넷 모집인원"] != listed.get("capacity_total")
        or integer_fields["현재신청자수"] != listed.get("capacity_current")
    ):
        raise TongyeongContractError(
            f"municipal library {identity} detail capacity mismatch"
        )
    apply_start, apply_end = _date_pair(
        fields["접수 기간"], f"municipal library {identity} detail application"
    )
    start, end = _date_pair(
        fields["강좌 기간"], f"municipal library {identity} detail education"
    )
    if (apply_start, apply_end) != (
        listed.get("apply_start"),
        listed.get("apply_end"),
    ):
        raise TongyeongContractError(
            f"municipal library {identity} application period mismatch"
        )
    if (start, end) != (listed.get("start"), listed.get("end")):
        raise TongyeongContractError(
            f"municipal library {identity} education period mismatch"
        )
    if _normalized(fields["강좌 일시"]) != _normalized(listed.get("schedule")):
        raise TongyeongContractError(
            f"municipal library {identity} schedule mismatch"
        )
    source_status = _clean(listed.get("source_status"))
    status = _LIBRARY_STATUS_MAP[source_status]
    if status == "OPEN" and not (apply_start <= cutoff <= apply_end):
        raise TongyeongContractError(
            f"municipal library {identity} open status/date mismatch"
        )
    if status == "SCHEDULED" and cutoff > apply_start:
        raise TongyeongContractError(
            f"municipal library {identity} scheduled status/date mismatch"
        )
    control, application_url = _library_detail_receive_control(box, listed)
    raw_venue = _clean(fields["강좌 장소"])
    venue = "" if raw_venue in {"0", "없음", "-"} else raw_venue
    branch = _library_exact_branch(
        _clean(listed.get("broad_branch")), raw_venue, identity
    )
    fee = _clean(fields["수강료"])
    capacity_total = integer_fields["인터넷 모집인원"]
    capacity_current = integer_fields["현재신청자수"]
    waitlist_total = integer_fields["대기자 모집인원"]
    row: dict[str, Any] = {
        "provider": TONGYEONG_LIBRARY_PROVIDER,
        "provider_course_id": (
            f"{TONGYEONG_LIBRARY_PROVIDER}:"
            f"{_clean(listed.get('lg_code'))}:{_clean(listed.get('le_code'))}"
        ),
        "prefer_incoming_provider_course_id": True,
        "title": _clean(listed.get("title")),
        "description": _clean(listed.get("title")),
        "branch": branch,
        "branch_code": _branch_code("tongyeong-library", branch),
        "preserve_branch": True,
        "category": "도서관 문화강좌",
        "program_type": "교육",
        "raw_url": _clean(listed.get("detail_url")),
        "application_url": application_url,
        "application_type": (
            "ONLINE_RESERVATION_LOGIN_REQUIRED" if control else "INFO_ONLY"
        ),
        "application_method": "온라인" if control else "",
        "application_methods": ["온라인"] if control else [],
        "reservation_available": control,
        "status": status,
        "fee": fee,
        "fee_amount": _fee_amount(fee),
        "period": f"{start.isoformat()} ~ {end.isoformat()}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "apply_period": fields["접수 기간"],
        "apply_start": apply_start.isoformat(),
        "apply_end": apply_end.isoformat(),
        "schedule_raw": fields["강좌 일시"],
        "capacity": f"{capacity_total}명",
        "capacity_current": capacity_current,
        "capacity_total": capacity_total,
        "waitlist_total": waitlist_total,
        "target": fields["대상"],
        "venue": venue,
        "venue_name": venue,
        "address": "",
        "venue_address": "",
        "collection_category": "도서관",
        "domain_category": "교육·강좌",
        "operator_type": "지자체 공공도서관",
        "source_group": "library",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": TONGYEONG_LIBRARY_PARSER,
        "municipality_code": TONGYEONG_MUNICIPALITY_CODE,
        "municipality_full_name": TONGYEONG_MUNICIPALITY_NAME,
        "raw_fields": {
            "identity": identity,
            "lg_code": _clean(listed.get("lg_code")),
            "le_code": _clean(listed.get("le_code")),
            "list_page": listed.get("list_page"),
            "source_branch_tab": _clean(listed.get("broad_branch")),
            "source_status": source_status,
            "source_capacity_current": capacity_current,
            "source_capacity_total": capacity_total,
            "source_waitlist_total": waitlist_total,
            "detail_verified": True,
            "application_control_present": control,
            "application_control_identity": identity if control else "",
            "private_registration_check_identity_verified_without_request": bool(
                listed.get("private_check_verified")
            ),
            "source_identity_anomaly": False,
            "service_family": "education",
        },
        "_source_end_date": end,
    }
    return row


def _library_content_anomalies(
    rows: list[dict[str, Any]],
) -> list[list[str]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        signature = (
            _clean(row.get("title")),
            _clean(row.get("target")),
            row.get("capacity_total"),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("schedule_raw")),
            _clean(row.get("venue")),
            _clean(row.get("fee")),
        )
        grouped.setdefault(signature, []).append(row)
    anomalies: list[list[str]] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        branches = {_clean(row.get("branch")) for row in group}
        if len(branches) < 2:
            continue
        identities = [
            _clean(row.get("raw_fields", {}).get("identity")) for row in group
        ]
        anomalies.append(identities)
        for row in group:
            row["raw_fields"]["source_identity_anomaly"] = True
    return anomalies


def collect_tongyeong_municipal_library(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect every municipal-library culture-course page and current detail."""

    meta = _base_meta(
        "city_municipal_library",
        TONGYEONG_LIBRARY_PARSER,
        TONGYEONG_LIBRARY_LIST_URL,
    )
    if not is_tongyeong_municipal_library_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact Tongyeong municipal-library owner"
        )
        return [], TONGYEONG_LIBRARY_PARSER, meta
    if not _validate_limits(timeout, max_pages, detail_limit):
        meta.update(
            {
                "source_cap_reached": True,
                "configured_collection_error": "invalid collection limits",
            }
        )
        return [], TONGYEONG_LIBRARY_PARSER, meta
    try:
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], TONGYEONG_LIBRARY_PARSER, meta

    current_factory = session_factory or _default_session_factory
    current_requester = requester or _default_requester
    session = current_factory()
    errors: list[str] = []
    result: list[dict[str, Any]] = []
    try:
        first = _parse_library_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_library_page_url(1),
                timeout,
            ),
            1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        declared_last = first.declared_last
        meta.update(
            {
                "declared_last_page": declared_last,
                "sentinel_page": declared_last + 1,
                "sentinel_mode": "structural_empty_tbody",
            }
        )
        if declared_last > max_pages:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"max_pages cap allows {max_pages} of "
                        f"{declared_last} declared municipal-library pages"
                    ),
                }
            )
            return [], TONGYEONG_LIBRARY_PARSER, meta
        pages: list[_LibraryPage] = [first]
        for page_number in range(2, declared_last + 1):
            page = _parse_library_page(
                _request_soup(
                    session,
                    current_requester,
                    tongyeong_library_page_url(page_number),
                    timeout,
                ),
                page_number,
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            pages.append(page)
        for page in pages:
            if (
                page.structural_empty
                or page.reported_page != page.requested_page
                or page.declared_last != declared_last
            ):
                errors.append(
                    f"municipal-library page {page.requested_page} boundary mismatch"
                )
        for page in pages[:-1]:
            if len(page.rows) != TONGYEONG_LIBRARY_PAGE_SIZE:
                errors.append(
                    f"municipal-library page {page.requested_page} is not full"
                )
        if not (1 <= len(pages[-1].rows) <= TONGYEONG_LIBRARY_PAGE_SIZE):
            errors.append("municipal-library last data page cardinality invalid")

        sentinel = _parse_library_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_library_page_url(declared_last + 1),
                timeout,
            ),
            declared_last + 1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        if (
            not sentinel.structural_empty
            or sentinel.rows
            or sentinel.reported_page is not None
            or sentinel.declared_last != declared_last
        ):
            errors.append(
                "municipal-library post-last page was not the structural empty sentinel"
            )

        boundary_pages = [pages[0]]
        if pages[-1].reported_page != pages[0].reported_page:
            boundary_pages.append(pages[-1])
        for original in boundary_pages:
            rechecked = _parse_library_page(
                _request_soup(
                    session,
                    current_requester,
                    tongyeong_library_page_url(original.requested_page),
                    timeout,
                ),
                original.requested_page,
            )
            meta["list_requests"] += 1
            meta["pages"] += 1
            meta["stability_rechecks"] += 1
            if _library_signature(rechecked) != _library_signature(original):
                errors.append(
                    f"municipal-library page {original.requested_page} "
                    "stability recheck changed"
                )
        sentinel_recheck = _parse_library_page(
            _request_soup(
                session,
                current_requester,
                tongyeong_library_page_url(declared_last + 1),
                timeout,
            ),
            declared_last + 1,
        )
        meta["list_requests"] += 1
        meta["pages"] += 1
        meta["stability_rechecks"] += 1
        if _library_signature(sentinel_recheck) != _library_signature(sentinel):
            errors.append(
                "municipal-library post-last sentinel stability recheck changed"
            )
        meta["required_list_requests"] = (
            declared_last + 1 + len(boundary_pages) + 1
        )

        listed = [row for page in pages for row in page.rows]
        identities = [_clean(row.get("identity")) for row in listed]
        duplicate_count = len(identities) - len(set(identities))
        if duplicate_count:
            errors.append(
                f"{duplicate_count} duplicate municipal-library official identities"
            )
        reversed_rows = [
            row for row in listed if not bool(row.get("chronology_valid"))
        ]
        blocking_reversed = [
            _clean(row.get("identity"))
            for row in reversed_rows
            if row.get("end") >= cutoff
        ]
        if blocking_reversed:
            errors.append(
                "current/future municipal-library rows have reversed dates: "
                + ", ".join(blocking_reversed)
            )
        missing_schedule_rows = [
            row for row in listed if not _clean(row.get("schedule"))
        ]
        blocking_missing_schedule = [
            _clean(row.get("identity"))
            for row in missing_schedule_rows
            if row.get("end") >= cutoff
        ]
        if blocking_missing_schedule:
            errors.append(
                "current/future municipal-library rows have no schedule: "
                + ", ".join(blocking_missing_schedule)
            )
        missing_target_rows = [
            row for row in listed if not _clean(row.get("target"))
        ]
        blocking_missing_target = [
            _clean(row.get("identity"))
            for row in missing_target_rows
            if row.get("end") >= cutoff
        ]
        if blocking_missing_target:
            errors.append(
                "current/future municipal-library rows have no target: "
                + ", ".join(blocking_missing_target)
            )
        missing_capacity_rows = [
            row
            for row in listed
            if not isinstance(row.get("capacity_total"), int)
            or not isinstance(row.get("capacity_current"), int)
        ]
        blocking_missing_capacity = [
            _clean(row.get("identity"))
            for row in missing_capacity_rows
            if row.get("end") >= cutoff
        ]
        if blocking_missing_capacity:
            errors.append(
                "current/future municipal-library rows have invalid capacity: "
                + ", ".join(blocking_missing_capacity)
            )
        missing_private_markers = [
            row for row in listed if not bool(row.get("private_check_verified"))
        ]
        blocking_missing_private_markers = [
            _clean(row.get("identity"))
            for row in missing_private_markers
            if row.get("end") >= cutoff
        ]
        if blocking_missing_private_markers:
            errors.append(
                "current/future municipal-library rows have no private-check "
                "identity marker: " + ", ".join(blocking_missing_private_markers)
            )
        list_complete = bool(
            not errors
            and meta["list_requests"] == meta["required_list_requests"]
            and meta["stability_rechecks"] == len(boundary_pages) + 1
        )
        meta.update(
            {
                "data_pages": declared_last,
                "source_total": len(listed),
                "source_rows": len(listed),
                "source_branch_tab_counts": dict(
                    Counter(_clean(row.get("broad_branch")) for row in listed)
                ),
                "source_status_counts": dict(
                    Counter(_clean(row.get("source_status")) for row in listed)
                ),
                "identity_duplicate_count": duplicate_count,
                "archived_date_anomaly_count": len(reversed_rows)
                - len(blocking_reversed),
                "archived_date_anomaly_identities": [
                    _clean(row.get("identity"))
                    for row in reversed_rows
                    if row.get("end") < cutoff
                ],
                "archived_missing_schedule_count": len(missing_schedule_rows)
                - len(blocking_missing_schedule),
                "archived_missing_schedule_identities": [
                    _clean(row.get("identity"))
                    for row in missing_schedule_rows
                    if row.get("end") < cutoff
                ],
                "archived_missing_target_count": len(missing_target_rows)
                - len(blocking_missing_target),
                "archived_missing_target_identities": [
                    _clean(row.get("identity"))
                    for row in missing_target_rows
                    if row.get("end") < cutoff
                ],
                "archived_invalid_capacity_count": len(missing_capacity_rows)
                - len(blocking_missing_capacity),
                "archived_invalid_capacity_identities": [
                    _clean(row.get("identity"))
                    for row in missing_capacity_rows
                    if row.get("end") < cutoff
                ],
                "archived_missing_private_marker_count": len(
                    missing_private_markers
                )
                - len(blocking_missing_private_markers),
                "archived_missing_private_marker_identities": [
                    _clean(row.get("identity"))
                    for row in missing_private_markers
                    if row.get("end") < cutoff
                ],
                "pagination_complete": list_complete,
            }
        )
        if not list_complete:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
            return [], TONGYEONG_LIBRARY_PARSER, meta

        candidates = [row for row in listed if row["end"] >= cutoff]
        current_page_counts = Counter(
            int(row.get("list_page", 0)) for row in candidates
        )
        meta.update(
            {
                "current_candidate_count": len(candidates),
                "current_page_counts": dict(sorted(current_page_counts.items())),
                "late_current_pages": {
                    page: count
                    for page, count in sorted(current_page_counts.items())
                    if page > 3
                },
                "archived_rows_skipped_before_detail": len(listed)
                - len(candidates),
            }
        )
        if len(candidates) > detail_limit:
            meta.update(
                {
                    "source_cap_reached": True,
                    "configured_collection_error": (
                        f"detail_limit cap allows {detail_limit} of "
                        f"{len(candidates)} current/future municipal-library details"
                    ),
                }
            )
            return [], TONGYEONG_LIBRARY_PARSER, meta
        meta["detail_attempts"] = len(candidates)
        detailed: list[dict[str, Any]] = []
        for listed_row in candidates:
            identity = _clean(listed_row.get("identity"))
            try:
                soup = _request_soup(
                    session,
                    current_requester,
                    _clean(listed_row.get("detail_url")),
                    timeout,
                    {
                        "Referer": tongyeong_library_page_url(
                            int(listed_row.get("list_page", 1))
                        )
                    },
                )
                detailed.append(
                    _parse_library_detail(listed_row, soup, cutoff)
                )
                meta["detail_pages"] += 1
                meta["pages"] += 1
            except Exception as exc:
                errors.append(
                    "municipal-library detail "
                    f"{identity}: {type(exc).__name__}: {_clean(exc)}"
                )
                meta["detail_errors"] += 1
        details_complete = bool(
            not errors
            and meta["detail_attempts"] == meta["detail_pages"]
            and len(detailed) == len(candidates)
        )
        controls_complete = bool(
            details_complete
            and all(
                row.get("raw_fields", {}).get("detail_verified")
                and row.get("raw_fields", {}).get(
                    "private_registration_check_identity_verified_without_request"
                )
                for row in detailed
            )
        )
        anomaly_groups: list[list[str]] = []
        if details_complete and controls_complete:
            anomaly_groups = _library_content_anomalies(detailed)
            result = _finalize_rows(
                detailed, _LIBRARY_SAFE_RAW_FIELDS, dedupe_rows, errors
            )
        snapshot_complete = bool(
            list_complete and details_complete and controls_complete and not errors
        )
        if not snapshot_complete:
            result = []
        meta.update(
            {
                "current_source_count": len(detailed),
                "expired_count": len(listed) - len(detailed),
                "current_source_status_counts": dict(
                    Counter(
                        _clean(row.get("raw_fields", {}).get("source_status"))
                        for row in detailed
                    )
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "application_control_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "application_control_present"
                        )
                    )
                    for row in detailed
                ),
                "source_identity_anomaly_count": len(anomaly_groups),
                "source_identity_anomaly_groups": anomaly_groups,
                "details_complete": details_complete,
                "application_controls_complete": controls_complete,
                "snapshot_complete": snapshot_complete,
                "full_snapshot_validated": snapshot_complete,
                "returned_count": len(result),
                "no_current_data": bool(snapshot_complete and not detailed),
                "no_current_reason": (
                    "all Tongyeong municipal-library courses are expired"
                    if snapshot_complete and not detailed
                    else ""
                ),
                "municipality_coverage": [TONGYEONG_MUNICIPALITY_CODE],
                "discovery_audit": dict(TONGYEONG_DISCOVERY_AUDIT),
                "owner_boundary_audit": {
                    key: dict(value)
                    for key, value in TONGYEONG_OWNER_BOUNDARY_AUDIT.items()
                },
                "pii_fields_never_persisted": list(
                    TONGYEONG_PII_FIELDS_NEVER_PERSISTED
                ),
                "configured_collection_error": "; ".join(
                    dict.fromkeys(errors)
                ),
            }
        )
        return result, TONGYEONG_LIBRARY_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = (
            f"{type(exc).__name__}: {_clean(exc)}"
        )
        meta["pagination_complete"] = False
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        return [], TONGYEONG_LIBRARY_PARSER, meta
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def collect_tongyeong_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = 100,
    detail_limit: int = 500,
    today: Optional[date | datetime | str] = None,
    session_factory: Optional[SessionFactory] = None,
    requester: Optional[Requester] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Dispatch only one exact, ownership-separated Tongyeong ledger."""

    kwargs = {
        "timeout": timeout,
        "max_pages": max_pages,
        "detail_limit": detail_limit,
        "today": today,
        "session_factory": session_factory,
        "requester": requester,
        "dedupe_rows": dedupe_rows,
    }
    if is_tongyeong_gne_library_target(target):
        return collect_tongyeong_gne_library(target, **kwargs)
    if is_tongyeong_city_lifelong_target(target):
        return collect_tongyeong_city_lifelong(target, **kwargs)
    if is_tongyeong_municipal_library_target(target):
        return collect_tongyeong_municipal_library(target, **kwargs)
    meta = _base_meta("unknown", TONGYEONG_PARSER, "")
    meta["configured_collection_error"] = (
        "target does not match an exact Tongyeong education owner"
    )
    return [], TONGYEONG_PARSER, meta


collect = collect_tongyeong_education


__all__ = [
    "TONGYEONG_CITY_CANDIDATE_ID",
    "TONGYEONG_CITY_HOME_URL",
    "TONGYEONG_CITY_LIST_URL",
    "TONGYEONG_CITY_PARSER",
    "TONGYEONG_CITY_PROVIDER",
    "TONGYEONG_DISCOVERY_AUDIT",
    "TONGYEONG_GNE_ADDRESS",
    "TONGYEONG_GNE_BRANCH",
    "TONGYEONG_GNE_CANDIDATE_ID",
    "TONGYEONG_GNE_LIST_URL",
    "TONGYEONG_GNE_MENU_URL",
    "TONGYEONG_GNE_PARSER",
    "TONGYEONG_GNE_PROVIDER",
    "TONGYEONG_LIBRARY_LIST_URL",
    "TONGYEONG_LIBRARY_PARSER",
    "TONGYEONG_LIBRARY_PROVIDER",
    "TONGYEONG_MUNICIPALITY_CODE",
    "TONGYEONG_MUNICIPALITY_NAME",
    "TONGYEONG_OWNER_BOUNDARY_AUDIT",
    "TONGYEONG_PARSER",
    "TONGYEONG_PII_FIELDS_NEVER_PERSISTED",
    "TongyeongContractError",
    "collect",
    "collect_tongyeong_city_lifelong",
    "collect_tongyeong_education",
    "collect_tongyeong_gne_library",
    "collect_tongyeong_municipal_library",
    "is_target",
    "is_tongyeong_city_lifelong_target",
    "is_tongyeong_education_target",
    "is_tongyeong_gne_library_target",
    "is_tongyeong_municipal_library_target",
    "tongyeong_city_detail_url",
    "tongyeong_city_page_url",
    "tongyeong_gne_detail_url",
    "tongyeong_gne_page_url",
    "tongyeong_library_detail_url",
    "tongyeong_library_page_url",
]
