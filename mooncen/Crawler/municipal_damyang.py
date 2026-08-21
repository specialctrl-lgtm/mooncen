"""Fail-closed collectors for Damyang-gun's two official education owners.

The municipality-wide audit found two disjoint owners:

* Damyang Library publishes three complete ``lecture.es`` catalogues
  (reading/culture, lifelong learning, and humanities).  The existing
  lifelong-learning provider is retained as the primary identity and the two
  sibling catalogue providers must be disabled as included scopes.
* Damyang Lifelong Learning Center publishes a separate county catalogue.  Its
  HTML is only a shell; the rows and details are public JSON endpoints invoked
  by that shell.  This owner was absent from discovery and therefore needs a
  new deterministic provider when centrally integrated.

Both collectors enumerate all history, require an immediate empty sentinel
and a stable first-page recheck, then retain only courses whose education end
date is current/future.  Detail extraction is allowlisted.  Remarks, contacts,
instructors, attachments, images, free-form content, applicant forms and
private application payloads are never persisted.

An important ownership correction is recorded here: requesting Boseong's
``a80402000000`` menu through the ``dylib`` hostname returns a page whose title,
canonical URL and course identities all belong to Boseong Library.  It is a
cross-host duplicate of ``bslib.jne.go.kr`` and is not a Damyang owner.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from html import unescape
import hashlib
import json
import math
import re
import ssl
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


DAMYANG_MUNICIPALITY_CODE = "1271000000"
DAMYANG_MUNICIPALITY_NAME = "전남광주통합특별시 담양군"

# Existing primary library provider.  The two other Damyang Library providers
# are included scopes and must not be scheduled independently after routing.
DAMYANG_LIBRARY_PROVIDER = "MUNI_DYLIB_JNE_GO_KR_1412DDEF"
DAMYANG_READING_PROVIDER = "MUNI_DYLIB_JNE_GO_KR_A2AEEC45"
DAMYANG_HUMANITIES_PROVIDER = "MUNI_DYLIB_JNE_GO_KR_A99A023A"
DAMYANG_CROSS_HOST_A804_PROVIDER = "MUNI_DYLIB_JNE_GO_KR_0EC67D8E"
DAMYANG_BOSEONG_PROVIDER = "MUNI_BSLIB_JNE_GO_KR_34227E33"

DAMYANG_LIBRARY_HOST = "dylib.jne.go.kr"
DAMYANG_LIBRARY_PATH = "/lecture.es"
DAMYANG_LIBRARY_BRANCH = "전남광주통합특별시교육청담양도서관"
DAMYANG_LIBRARY_PAGE_SIZE = 100
DAMYANG_LIBRARY_LOGIN_PATH = "/login_search.es"
DAMYANG_LIBRARY_LOGIN_SID = "a6"
DAMYANG_LIBRARY_TLS_CIPHER = "AES256-GCM-SHA384"

DAMYANG_LIBRARY_URL = (
    "https://dylib.jne.go.kr/lecture.es?mid=a60402000000"
)
DAMYANG_READING_URL = (
    "https://dylib.jne.go.kr/lecture.es?mid=a60202000000"
)
DAMYANG_HUMANITIES_URL = (
    "https://dylib.jne.go.kr/lecture.es?mid=a61102000000"
)
DAMYANG_HUMANITIES_INTRO_URL = (
    "https://dylib.jne.go.kr/menu.es?mid=a61101000000"
)
DAMYANG_CROSS_HOST_A804_URL = (
    "https://dylib.jne.go.kr/lecture.es?mid=a80402000000"
)
DAMYANG_BOSEONG_CANONICAL_URL = (
    "https://bslib.jne.go.kr/lecture.es?mid=a80402000000"
)

# Separate county lifelong-learning owner.  This is the exact public landing
# URL and the suffix follows stable_provider("MUNI", URL).
DAMYANG_LIFELONG_URL = (
    "https://www.damyang.go.kr/board/list?"
    "boardId=BBS_0000098&boardType=special&contentsSid=264&"
    "domainId=DOM_0000011&menuCd=DOM_000001101001000000"
)
DAMYANG_LIFELONG_PROVIDER = (
    "MUNI_WWW_DAMYANG_GO_KR_"
    + hashlib.sha1(DAMYANG_LIFELONG_URL.encode("utf-8")).hexdigest()[:8].upper()
)
DAMYANG_LIFELONG_BRANCH = "담양 평생학습센터"
DAMYANG_LIFELONG_HOST = "www.damyang.go.kr"
DAMYANG_LIFELONG_LIST_API_PATH = "/board/getContentsList"
DAMYANG_LIFELONG_DETAIL_API_PATH = "/board/getBoardDetail"
DAMYANG_LIFELONG_DETAIL_PATH = "/board/detail"
DAMYANG_LIFELONG_APPLICATION_PATH = "/board/write"
DAMYANG_LIFELONG_DOMAIN_ID = "DOM_0000011"
DAMYANG_LIFELONG_BOARD_ID = "BBS_0000098"
DAMYANG_LIFELONG_CONTENTS_SID = "264"
DAMYANG_LIFELONG_MENU_CD = "DOM_000001101001000000"
DAMYANG_LIFELONG_PAGE_SIZE = 100

DAMYANG_COUNTY_RESERVE_URL = "https://www.damyang.go.kr/reserve/index.damyang"
DAMYANG_COUNTY_RESERVE_PROVIDER = "MUNI_WWW_DAMYANG_GO_KR_B89D979C"
DAMYANG_CULTURE_GUIDE_PROVIDER = "MUNI_WWW_DAMYANG_GO_KR_E4688AD3"
DAMYANG_CULTURE_GUIDE_URL = (
    "https://www.damyang.go.kr/menu/goToContentsPage?contentsSid=2075&"
    "domainId=DOM_0000024&menuCd=DOM_000002424000000001"
)
DAMYANG_JUKNOKWON_URL = "https://www.juknokwon.go.kr/index.juknok"

DAMYANG_LIBRARY_PARSER = (
    "damyang_library_three_complete_catalogues+empty_sentinels+stable_page1+"
    "current_detail_status_capacity+login_gate+pii_allowlist"
)
DAMYANG_LIFELONG_PARSER = (
    "damyang_lifelong_public_json_all_pages+empty_sentinel+stable_page1+"
    "current_safe_detail+date_bound_application_controls+pii_allowlist"
)
DAMYANG_PARSER = "damyang_two_disjoint_education_owners_dispatch"

DAMYANG_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    "MUNI_IR_C84B2B6EBCBA": {
        "decision": "retain_as_primary_and_expand_to_three_library_catalogues",
        "provider": DAMYANG_LIBRARY_PROVIDER,
        "url": DAMYANG_LIBRARY_URL + "&act=view",
        "canonical_url": DAMYANG_LIBRARY_URL,
        "owner": DAMYANG_LIBRARY_PROVIDER,
    },
    "MUNI_IR_569B1B61A610": {
        "decision": "include_scope_under_primary_disable_separate_schedule",
        "provider": DAMYANG_READING_PROVIDER,
        "url": DAMYANG_READING_URL + "&act=view",
        "canonical_url": DAMYANG_READING_URL,
        "owner": DAMYANG_LIBRARY_PROVIDER,
    },
    "MUNI_IR_EF8522608DFF": {
        "decision": "include_scope_under_primary_disable_separate_schedule",
        "provider": DAMYANG_HUMANITIES_PROVIDER,
        "url": DAMYANG_HUMANITIES_INTRO_URL,
        "canonical_url": DAMYANG_HUMANITIES_URL,
        "owner": DAMYANG_LIBRARY_PROVIDER,
    },
    "CROSS_HOST_A804_CONFIGURATION": {
        "decision": "disable_cross_host_duplicate_of_boseong_canonical",
        "provider": DAMYANG_CROSS_HOST_A804_PROVIDER,
        "url": DAMYANG_CROSS_HOST_A804_URL,
        "canonical_url": DAMYANG_BOSEONG_CANONICAL_URL,
        "owner": DAMYANG_BOSEONG_PROVIDER,
        "live_title_owner": "전남광주통합특별시교육청보성도서관",
        "reason": (
            "dylib hostname is an alias only; og:url, title and rows are Boseong"
        ),
    },
    "NEW_OFFICIAL_COUNTY_LIFELONG_OWNER": {
        "decision": "add_new_separate_structured_course_owner",
        "provider": DAMYANG_LIFELONG_PROVIDER,
        "url": DAMYANG_LIFELONG_URL,
        "canonical_url": DAMYANG_LIFELONG_URL,
        "owner": DAMYANG_LIFELONG_PROVIDER,
    },
    "COUNTY_INTEGRATED_RESERVATION": {
        "decision": "exclude_from_education_current_rebuild_has_no_course_catalogue",
        "provider": DAMYANG_COUNTY_RESERVE_PROVIDER,
        "url": DAMYANG_COUNTY_RESERVE_URL,
        "owner": "damyang_facility_tour_reservation",
    },
    "COUNTY_CULTURE_GUIDE": {
        "decision": "keep_separate_tour_experience_owner",
        "provider": DAMYANG_CULTURE_GUIDE_PROVIDER,
        "url": DAMYANG_CULTURE_GUIDE_URL,
        "owner": "damyang_culture_tour_guide",
    },
    "JUKNOKWON": {
        "decision": "exclude_accommodation_owner",
        "provider": "SEPARATE_JUKNOKWON",
        "url": DAMYANG_JUKNOKWON_URL,
        "owner": "juknokwon_hanok_accommodation",
    },
}

DAMYANG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    DAMYANG_LIBRARY_PROVIDER: {
        "decision": "retain_existing_primary_library_owner_union_three_scopes",
        "exact_branch": DAMYANG_LIBRARY_BRANCH,
        "catalogues": (
            DAMYANG_READING_URL,
            DAMYANG_LIBRARY_URL,
            DAMYANG_HUMANITIES_URL,
        ),
        "included_provider_aliases": (
            DAMYANG_READING_PROVIDER,
            DAMYANG_HUMANITIES_PROVIDER,
        ),
    },
    DAMYANG_LIFELONG_PROVIDER: {
        "decision": "new_separate_county_lifelong_owner",
        "exact_branch": DAMYANG_LIFELONG_BRANCH,
        "catalogues": (DAMYANG_LIFELONG_URL,),
    },
    DAMYANG_CROSS_HOST_A804_PROVIDER: {
        "decision": "disable_duplicate_do_not_attribute_to_damyang",
        "exact_branch": "전남광주통합특별시교육청보성도서관",
        "catalogues": (),
        "duplicate_of": DAMYANG_BOSEONG_PROVIDER,
    },
}

DAMYANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-21",
    "coverage_state": "review",
    "library_source_totals": {
        "reading_culture": 10,
        "lifelong": 10,
        "humanities": 5,
    },
    "library_page_counts": {
        "reading_culture": [10],
        "lifelong": [10],
        "humanities": [5],
    },
    "library_current_or_future": {
        "reading_culture": 2,
        "lifelong": 10,
        "humanities": 2,
    },
    "library_total_current_or_future": 14,
    "library_expired": 11,
    "library_required_list_requests": 9,
    "library_cross_scope_identity_overlap": 0,
    "library_current_status_counts": {
        "신청하기": 5,
        "대기자신청하기": 5,
        "마감": 3,
        "접수전": 1,
    },
    "lifelong_source_rows": 284,
    "lifelong_page_counts": [100, 100, 84],
    "lifelong_current_or_future": 4,
    "lifelong_expired_or_quarantined": 280,
    "lifelong_historical_incomplete_date_rows": 5,
    "lifelong_required_api_list_requests": 5,
    "lifelong_current_exact_institutions": {
        "담양군청": 3,
        "주민자치센터": 1,
    },
    "cross_host_a804_is_boseong_duplicate": True,
    "conclusion": (
        "schedule the three library scopes once under the existing primary "
        "provider; add the separate county lifelong owner; disable a602/a611 "
        "separate schedules and the a804 cross-host Boseong duplicate"
    ),
}

DAMYANG_PII_FIELDS_DISCARDED = (
    "썸네일 및 이미지 URL",
    "비고 및 자유본문",
    "강사명/강사소개",
    "담당자/연락처/계좌정보",
    "전화번호/이메일",
    "첨부파일 및 다운로드 URL",
    "강의 계획서/교육 일정표",
    "로그인 및 신청 form payload",
    "신청자 개인정보",
    "source HTML/JSON payload",
)


@dataclass(frozen=True)
class DamyangLibrarySource:
    code: str
    mid: str
    url: str
    menu: str
    program_type: str


DAMYANG_LIBRARY_SOURCES: tuple[DamyangLibrarySource, ...] = (
    DamyangLibrarySource(
        "reading_culture",
        "a60202000000",
        DAMYANG_READING_URL,
        "독서문화진흥",
        "독서문화 강좌",
    ),
    DamyangLibrarySource(
        "lifelong",
        "a60402000000",
        DAMYANG_LIBRARY_URL,
        "평생학습",
        "평생학습 강좌",
    ),
    DamyangLibrarySource(
        "humanities",
        "a61102000000",
        DAMYANG_HUMANITIES_URL,
        "인문학강좌",
        "인문학 강좌",
    ),
)
_LIBRARY_SOURCE_BY_CODE = {source.code: source for source in DAMYANG_LIBRARY_SOURCES}

SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"[1-9]\d*")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_DATETIME_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})"
    r"(?:\s+|T)(\d{1,2})\s*(?::|시)\s*(\d{1,2})(?:\s*분)?(?!\d)"
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

_LIBRARY_LIST_HEADERS = (
    "연번",
    "이미지",
    "강좌명",
    "대상",
    "운영기간",
    "인터넷접수",
    "신청 / 정원 (대기인원)",
    "상태",
)
_LIBRARY_LIST_LABELS = (
    "",
    "",
    "강좌명",
    "대상",
    "운영기간",
    "인터넷접수",
    "신청현황",
    "상태",
)
_LIBRARY_DETAIL_FIELDS = (
    "썸네일",
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
_LIBRARY_SAFE_DETAIL_FIELDS = frozenset(
    {
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
    }
)
_LIBRARY_STATUS_MAP: Mapping[str, str] = {
    "신청하기": "OPEN",
    "대기자신청하기": "OPEN",
    "접수전": "SCHEDULED",
    "마감": "CLOSED",
}
_COUNTY_LANDING_HEADERS = (
    "번호",
    "강좌명/강사명/신청기간/교육기간",
    "교육기관",
    "인원신청/정원",
    "수강료",
    "접수현황",
)
_SAFE_RAW_FIELDS = frozenset(
    {
        "owner_scope",
        "source_catalogue",
        "source_sequence",
        "source_identity",
        "source_status",
        "source_application_method",
        "list_schema_verified",
        "detail_schema_verified",
        "list_detail_verified",
        "capacity_verified",
        "application_control_verified",
        "login_gate_verified",
        "fee_evidence",
    }
)
_FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "비고",
        "강사",
        "강사명",
        "강사소개",
        "담당자",
        "전화번호",
        "이메일",
        "계좌",
        "첨부파일",
        "강의 계획서",
        "교육 일정표",
        "thumbnail",
        "image_url",
        "attachment",
        "instructor",
        "contact",
        "remarks",
        "dataContent",
        "source_html",
        "source_json",
        "form_payload",
    }
)


class DamyangContractError(ValueError):
    """Raised when an audited Damyang source contract changes."""


class DamyangLibraryTlsAdapter(HTTPAdapter):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.ssl_context = build_damyang_library_tls_context()
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)


def build_damyang_library_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(DAMYANG_LIBRARY_TLS_CIPHER)
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("verified TLS defaults unexpectedly unavailable")
    return context


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", _clean(value)).casefold()


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


def _query(url: str) -> tuple[Any, dict[str, str]]:
    parsed = urlparse(url)
    query: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in query:
            raise DamyangContractError("duplicate URL query parameter")
        query[key] = value
    return parsed, query


def _exact_https_url(value: Any, expected: str) -> bool:
    raw = _clean(value)
    if raw != expected:
        return False
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and not parsed.params
        and not parsed.fragment
    )


def is_damyang_library_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == DAMYANG_LIBRARY_PROVIDER
        and _exact_https_url(_target_value(target, "url"), DAMYANG_LIBRARY_URL)
    )


def is_damyang_lifelong_target(target: Any) -> bool:
    return bool(
        _clean(_target_value(target, "provider")) == DAMYANG_LIFELONG_PROVIDER
        and _exact_https_url(_target_value(target, "url"), DAMYANG_LIFELONG_URL)
    )


def is_damyang_target(target: Any) -> bool:
    return is_damyang_library_target(target) or is_damyang_lifelong_target(target)


is_target = is_damyang_target


def damyang_library_list_url(source_code: str, page: int = 1) -> str:
    source = _LIBRARY_SOURCE_BY_CODE.get(_clean(source_code))
    if source is None or isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("unknown source or invalid page")
    query = [("mid", source.mid)]
    if page > 1:
        query.append(("nPage", str(page)))
    return f"https://{DAMYANG_LIBRARY_HOST}{DAMYANG_LIBRARY_PATH}?{urlencode(query)}"


def damyang_library_detail_url(source_code: str, identity: Any) -> str:
    source = _LIBRARY_SOURCE_BY_CODE.get(_clean(source_code))
    identity = _clean(identity)
    if source is None or _IDENTITY_RE.fullmatch(identity) is None:
        raise ValueError("unknown source or invalid identity")
    return f"https://{DAMYANG_LIBRARY_HOST}{DAMYANG_LIBRARY_PATH}?" + urlencode(
        (("mid", source.mid), ("act", "view"), ("el_seq", identity))
    )


def _county_common_query(identity: Optional[str] = None) -> list[tuple[str, str]]:
    result = [
        ("domainId", DAMYANG_LIFELONG_DOMAIN_ID),
        ("boardId", DAMYANG_LIFELONG_BOARD_ID),
        ("contentsSid", DAMYANG_LIFELONG_CONTENTS_SID),
        ("menuCd", DAMYANG_LIFELONG_MENU_CD),
    ]
    if identity is not None:
        if _IDENTITY_RE.fullmatch(_clean(identity)) is None:
            raise ValueError("invalid county course identity")
        result.append(("dataSid", _clean(identity)))
    return result


def damyang_lifelong_api_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    begin = (page - 1) * DAMYANG_LIFELONG_PAGE_SIZE + 1
    query = [
        ("domainId", DAMYANG_LIFELONG_DOMAIN_ID),
        ("boardId", DAMYANG_LIFELONG_BOARD_ID),
        ("startDate", ""),
        ("endDate", ""),
        ("searchCondition1", ""),
        ("searchCondition2", ""),
        ("searchKeywordCon", ""),
        ("searchKeyword", ""),
        ("ROW_CNT", str(DAMYANG_LIFELONG_PAGE_SIZE)),
        ("BEGIN_ROW_IDX", str(begin)),
        ("CUR_PAGE_IDX", str(page)),
    ]
    return (
        f"https://{DAMYANG_LIFELONG_HOST}{DAMYANG_LIFELONG_LIST_API_PATH}?"
        + urlencode(query)
    )


def damyang_lifelong_detail_api_url(identity: Any) -> str:
    identity = _clean(identity)
    if _IDENTITY_RE.fullmatch(identity) is None:
        raise ValueError("invalid county course identity")
    return (
        f"https://{DAMYANG_LIFELONG_HOST}{DAMYANG_LIFELONG_DETAIL_API_PATH}?"
        + urlencode((("boardId", DAMYANG_LIFELONG_BOARD_ID), ("dataSid", identity)))
    )


def damyang_lifelong_detail_url(identity: Any) -> str:
    identity = _clean(identity)
    return (
        f"https://{DAMYANG_LIFELONG_HOST}{DAMYANG_LIFELONG_DETAIL_PATH}?"
        + urlencode(_county_common_query(identity))
    )


def damyang_lifelong_application_url(identity: Any) -> str:
    identity = _clean(identity)
    return (
        f"https://{DAMYANG_LIFELONG_HOST}{DAMYANG_LIFELONG_APPLICATION_PATH}?"
        + urlencode((*_county_common_query(identity), ("boardType", "register")))
    )


def _library_session_factory() -> requests.Session:
    session = requests.Session()
    session.mount("https://", DamyangLibraryTlsAdapter())
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": DAMYANG_LIBRARY_URL,
        }
    )
    return session


def _county_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": DAMYANG_LIFELONG_URL,
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    response = session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise DamyangContractError(
            f"unexpected HTTP status {getattr(response, 'status_code', None)}"
        )
    if getattr(response, "headers", {}).get("Location"):
        raise DamyangContractError("redirect response is not accepted")
    return response


def _payload_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, bytearray):
        payload = bytes(value)
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = getattr(value, "content", None)
        if payload is None:
            text = getattr(value, "text", None)
            payload = text.encode("utf-8") if isinstance(text, str) else None
    if not payload:
        raise DamyangContractError("empty HTTP response")
    if len(payload) > 5_000_000:
        raise DamyangContractError("HTTP response exceeds audited byte cap")
    return payload


def _coerce_soup(value: Any) -> BeautifulSoup:
    return BeautifulSoup(_payload_bytes(value), "lxml")


def _coerce_json(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        data = value
    else:
        try:
            data = json.loads(_payload_bytes(value).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DamyangContractError("invalid UTF-8 JSON response") from exc
    if not isinstance(data, Mapping):
        raise DamyangContractError("JSON root is not an object")
    return data


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
        attempts: int = 3,
    ) -> None:
        self.timeout = timeout
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.attempts = attempts
        self.requests = 0
        self.sessions_created = 0

    def _get(self, url: str) -> Any:
        error: Optional[Exception] = None
        for _attempt in range(self.attempts):
            session: Any = None
            try:
                session = self.session_factory()
                self.sessions_created += 1
                self.requests += 1
                return self.fetcher(session, url, self.timeout)
            except Exception as exc:
                error = exc
            finally:
                _close_quietly(session)
        assert error is not None
        raise error

    def html(self, url: str) -> BeautifulSoup:
        return _coerce_soup(self._get(url))

    def json(self, url: str) -> Mapping[str, Any]:
        return _coerce_json(self._get(url))


def _one(nodes: list[Any], label: str) -> Any:
    if len(nodes) != 1:
        raise DamyangContractError(f"expected one {label}, found {len(nodes)}")
    return nodes[0]


def _date_range(value: Any, label: str) -> tuple[str, str]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) != 2:
        raise DamyangContractError(f"{label} date range changed")
    try:
        start = date(*(int(part) for part in matches[0]))
        end = date(*(int(part) for part in matches[1]))
    except ValueError as exc:
        raise DamyangContractError(f"{label} contains invalid date") from exc
    if end < start:
        raise DamyangContractError(f"{label} date range reversed")
    return start.isoformat(), end.isoformat()


def _datetime_range(value: Any, label: str) -> tuple[str, str]:
    matches = _DATETIME_RE.findall(_clean(value))
    if len(matches) != 2:
        raise DamyangContractError(f"{label} datetime range changed")
    timezone = ZoneInfo("Asia/Seoul")
    try:
        start, end = (
            datetime(*(int(part) for part in match), tzinfo=timezone)
            for match in matches
        )
    except ValueError as exc:
        raise DamyangContractError(f"{label} contains invalid datetime") from exc
    if end < start:
        raise DamyangContractError(f"{label} datetime range reversed")
    return start.isoformat(), end.isoformat()


def _safe_date(value: Any) -> Optional[date]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _safe_datetime(value: Any) -> Optional[datetime]:
    raw = _clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return parsed.astimezone(ZoneInfo("Asia/Seoul"))


def _reference_datetime(value: Optional[date | datetime | str]) -> datetime:
    timezone = ZoneInfo("Asia/Seoul")
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone)
            if value.tzinfo is None
            else value.astimezone(timezone)
        )
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone)
    if value is not None:
        raw = _clean(value)
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("invalid reference date/datetime") from exc
        return (
            parsed.replace(tzinfo=timezone)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone)
        )
    return datetime.now(timezone)


def _today(value: Optional[date | datetime | str]) -> date:
    return _reference_datetime(value).date()


def _library_page_contract(
    source: DamyangLibrarySource, soup: BeautifulSoup, page: int
) -> Any:
    title = _one(soup.select("head > title"), f"{source.code} title")
    expected = f"글쓰기 | 수강 신청 | {source.menu} : {DAMYANG_LIBRARY_BRANCH}"
    if _clean(title.get_text(" ", strip=True)) != expected:
        raise DamyangContractError(f"{source.code} institution title changed")
    og = _one(soup.select("meta[property='og:url']"), f"{source.code} og:url")
    expected_og = damyang_library_list_url(source.code, page)
    if _clean(og.get("content")) != expected_og:
        raise DamyangContractError(f"{source.code} canonical page marker changed")
    form = _one(soup.select("form[name='srhForm']"), f"{source.code} search form")
    if _clean(form.get("method")).lower() != "post":
        raise DamyangContractError(f"{source.code} search method changed")
    action = urljoin(source.url, _clean(form.get("action")))
    parsed, query = _query(action)
    if (
        parsed.scheme != "https"
        or parsed.hostname != DAMYANG_LIBRARY_HOST
        or parsed.path != DAMYANG_LIBRARY_PATH
        or query != {"mid": source.mid}
    ):
        raise DamyangContractError(f"{source.code} search action changed")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type='hidden'][name]")
    }
    if hidden != {
        "actionUrl": DAMYANG_LIBRARY_PATH,
        "nPage": "" if page == 1 else str(page),
        "mid": source.mid,
        "act": "list",
        "b_list": str(DAMYANG_LIBRARY_PAGE_SIZE),
    }:
        raise DamyangContractError(f"{source.code} pagination form changed")
    keyword = _one(form.select("input[name='keyWord']"), f"{source.code} keyword")
    if _clean(keyword.get("value")):
        raise DamyangContractError(f"{source.code} search unexpectedly filtered")
    table = _one(soup.select("table.tstyle_list"), f"{source.code} table")
    headers = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in table.select("thead > tr > th, thead > tr > td")
    )
    if headers != _LIBRARY_LIST_HEADERS:
        raise DamyangContractError(f"{source.code} headers changed")
    return table


def _library_detail_href(
    source: DamyangLibrarySource, value: Any, page: int
) -> tuple[str, str]:
    absolute = urljoin(source.url, _clean(value))
    parsed, query = _query(absolute)
    try:
        port = parsed.port
    except ValueError as exc:
        raise DamyangContractError("invalid detail port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != DAMYANG_LIBRARY_HOST
        or port is not None
        or parsed.path != DAMYANG_LIBRARY_PATH
        or set(query) != {"mid", "act", "el_seq", "nPage"}
        or query.get("mid") != source.mid
        or query.get("act") != "view"
        or query.get("nPage") != ("" if page == 1 else str(page))
        or _IDENTITY_RE.fullmatch(query.get("el_seq", "")) is None
    ):
        raise DamyangContractError("detail URL escaped Damyang Library catalogue")
    identity = query["el_seq"]
    return identity, damyang_library_detail_url(source.code, identity)


def _validate_library_action(
    cell: Any, identity: str, status_text: str, *, detail: bool
) -> None:
    anchors = cell.select(":scope > a")
    markers = cell.select(":scope > span")
    if status_text in {"신청하기", "대기자신청하기"}:
        anchor = _one(anchors, f"course {identity} application action")
        if (
            _clean(anchor.get("href")) != "#"
            or _clean(anchor.get("onclick")) != "checkLogin(); return false;"
        ):
            raise DamyangContractError(f"course {identity} login action changed")
        expected_class = "w_app" if detail or status_text == "신청하기" else "w_tmp"
        span = _one(
            anchor.select(f":scope > span.{expected_class}"),
            f"course {identity} action marker",
        )
        if _clean(span.get_text(" ", strip=True)) != status_text or markers:
            raise DamyangContractError(f"course {identity} action label changed")
        return
    if anchors:
        raise DamyangContractError(f"course {identity} inactive status has action")
    expected = "w_wait" if status_text == "접수전" else "w_close"
    marker = _one(markers, f"course {identity} inactive marker")
    if marker.get("class") != [expected] or _clean(marker.get_text(" ", strip=True)) != status_text:
        raise DamyangContractError(f"course {identity} status marker changed")


def _parse_library_page(
    source: DamyangLibrarySource, soup: BeautifulSoup, page: int
) -> tuple[list[dict[str, Any]], bool]:
    table = _library_page_contract(source, soup, page)
    rows: list[dict[str, Any]] = []
    explicit_empty = False
    for tr in table.select("tbody > tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        number = _clean(cells[0].get_text(" ", strip=True))
        if not number.isdigit():
            if (
                len(cells) == 1
                and cells[0].get("class") == ["nodata"]
                and _clean(cells[0].get("colspan")) == "6"
                and _clean(cells[0].get_text(" ", strip=True))
                == "등록된 자료가 존재하지 않습니다."
            ):
                explicit_empty = True
                continue
            raise DamyangContractError(f"{source.code} non-numeric sequence")
        if len(cells) != len(_LIBRARY_LIST_HEADERS):
            raise DamyangContractError(f"{source.code} row {number} cells changed")
        labels = tuple(_clean(cell.get("aria-label")) for cell in cells)
        if labels != _LIBRARY_LIST_LABELS:
            raise DamyangContractError(f"{source.code} row {number} labels changed")
        image_cell = cells[1]
        if _clean(image_cell.get_text(" ", strip=True)) or len(image_cell.select(":scope > img")) != 1:
            raise DamyangContractError(f"{source.code} row {number} image cell changed")
        anchor = _one(cells[2].select(":scope > a[href]"), f"row {number} detail")
        identity, raw_url = _library_detail_href(source, anchor.get("href"), page)
        title = _clean(anchor.get_text(" ", strip=True))
        if not title or _normalized(cells[2].get_text(" ", strip=True)) != _normalized(title):
            raise DamyangContractError(f"course {identity} title changed")
        target = _clean(cells[3].get_text(" ", strip=True))
        raw_period = _clean(cells[4].get_text(" ", strip=True))
        raw_apply = _clean(cells[5].get_text(" ", strip=True))
        start, end = _date_range(raw_period, f"course {identity} operating")
        apply_start, apply_end = _date_range(raw_apply, f"course {identity} application")
        apply_start_at, apply_end_at = _datetime_range(
            raw_apply, f"course {identity} application"
        )
        capacity = _CAPACITY_RE.fullmatch(_clean(cells[6].get_text(" ", strip=True)))
        if capacity is None:
            raise DamyangContractError(f"course {identity} capacity changed")
        current, total, wait_current, wait_total = (
            int(part) for part in capacity.groups()
        )
        status_text = _clean(cells[7].get_text(" ", strip=True))
        if status_text not in _LIBRARY_STATUS_MAP:
            raise DamyangContractError(f"course {identity} unknown status {status_text!r}")
        _validate_library_action(cells[7], identity, status_text, detail=False)
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
                "apply_start_at": apply_start_at,
                "apply_end_at": apply_end_at,
                "capacity_current": current,
                "capacity_total": total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
                "source_status": status_text,
                "status": _LIBRARY_STATUS_MAP[status_text],
                "raw_url": raw_url,
            }
        )
    if rows and explicit_empty:
        raise DamyangContractError(f"{source.code} rows mixed with empty marker")
    return rows, explicit_empty


def _library_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    keys = (
        "source_sequence",
        "identity",
        "title",
        "target",
        "raw_period",
        "raw_apply_period",
        "apply_start_at",
        "apply_end_at",
        "capacity_current",
        "capacity_total",
        "waitlist_current",
        "waitlist_total",
        "source_status",
        "raw_url",
    )
    return tuple(tuple(row.get(key) for key in keys) for row in rows)


def _library_safe_detail(table: Any, identity: str) -> dict[str, str]:
    labels: list[str] = []
    values: dict[str, str] = {}
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
            raise DamyangContractError(f"course {identity} detail pairing changed")
        for index in range(0, len(nodes), 2):
            label = _clean(nodes[index].get_text(" ", strip=True))
            if not label or label in labels:
                raise DamyangContractError(f"course {identity} detail label changed")
            labels.append(label)
            if label in _LIBRARY_SAFE_DETAIL_FIELDS:
                values[label] = _clean(nodes[index + 1].get_text(" ", strip=True))
    if tuple(labels) != _LIBRARY_DETAIL_FIELDS:
        raise DamyangContractError(f"course {identity} detail fields changed")
    if set(values) != _LIBRARY_SAFE_DETAIL_FIELDS:
        raise DamyangContractError(f"course {identity} safe detail fields missing")
    return values


def _validate_library_login(soup: BeautifulSoup, identity: str) -> None:
    scripts = "\n".join(node.get_text("\n", strip=True) for node in soup.select("script"))
    bodies = re.findall(r"function\s+checkLogin\s*\(\s*\)\s*\{(.*?)\}", scripts, re.S)
    if len(bodies) != 1:
        raise DamyangContractError(f"course {identity} login gate changed")
    body = bodies[0]
    alerts = re.findall(r"alert\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", body)
    locations = re.findall(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", body)
    if alerts != ["로그인 후 이용할 수 있습니다."] or locations != [
        f"{DAMYANG_LIBRARY_LOGIN_PATH}?sid={DAMYANG_LIBRARY_LOGIN_SID}"
    ] or re.search(r"return\s+false\s*;", body) is None:
        raise DamyangContractError(f"course {identity} login destination changed")


def _library_branch_code() -> str:
    digest = hashlib.sha1(DAMYANG_LIBRARY_BRANCH.encode("utf-8")).hexdigest()[:12].upper()
    return f"{DAMYANG_LIBRARY_PROVIDER}:LIBRARY:{digest}"[:100]


def _parse_library_detail(
    source: DamyangLibrarySource,
    parent: Mapping[str, Any],
    soup: BeautifulSoup,
    target: Any,
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    title = _one(soup.select("head > title"), f"course {identity} title")
    expected = f"글쓰기 | 수강 신청 | {source.menu} : {DAMYANG_LIBRARY_BRANCH}"
    if _clean(title.get_text(" ", strip=True)) != expected:
        raise DamyangContractError(f"course {identity} detail owner changed")
    table = _one(soup.select("table.tstyle_write"), f"course {identity} detail table")
    values = _library_safe_detail(table, identity)
    form = _one(soup.select("form[name='insForm']"), f"course {identity} detail form")
    hidden = {
        _clean(node.get("name")): _clean(node.get("value"))
        for node in form.select("input[type='hidden'][name]")
    }
    if (
        _clean(form.get("method")).lower() != "post"
        or _clean(form.get("action")) != "/lecture.es&act=ins"
        or hidden != {"actionUrl": DAMYANG_LIBRARY_PATH, "nPage": "", "act": "list"}
    ):
        raise DamyangContractError(f"course {identity} detail form changed")
    _validate_library_login(soup, identity)
    if _normalized(values["강좌명"]) != _normalized(parent.get("title")):
        raise DamyangContractError(f"course {identity} title mismatch")
    if _normalized(values["대상"]) != _normalized(parent.get("target")):
        raise DamyangContractError(f"course {identity} target mismatch")
    start, end = _date_range(values["운영기간"], f"course {identity} detail operating")
    apply_start, apply_end = _date_range(
        values["신청기간"], f"course {identity} detail application"
    )
    apply_start_at, apply_end_at = _datetime_range(
        values["신청기간"], f"course {identity} detail application"
    )
    if (start, end) != (parent.get("start_date"), parent.get("end_date")):
        raise DamyangContractError(f"course {identity} operating period mismatch")
    if (apply_start, apply_end) != (parent.get("apply_start"), parent.get("apply_end")):
        raise DamyangContractError(f"course {identity} application period mismatch")
    if (apply_start_at, apply_end_at) != (
        parent.get("apply_start_at"),
        parent.get("apply_end_at"),
    ):
        raise DamyangContractError(
            f"course {identity} application datetime mismatch"
        )
    status_text = values["접수상태"]
    if status_text != parent.get("source_status"):
        raise DamyangContractError(f"course {identity} status mismatch")
    total_match = _DETAIL_CAPACITY_RE.fullmatch(values["모집인원"])
    current_match = _DETAIL_CAPACITY_RE.fullmatch(values["신청자"])
    if total_match is None or current_match is None:
        raise DamyangContractError(f"course {identity} detail capacity changed")
    total, wait_total = (int(value) for value in total_match.groups())
    current, wait_current = (int(value) for value in current_match.groups())
    if (current, total, wait_current, wait_total) != (
        parent.get("capacity_current"),
        parent.get("capacity_total"),
        parent.get("waitlist_current"),
        parent.get("waitlist_total"),
    ):
        raise DamyangContractError(f"course {identity} capacity mismatch")
    if values["신청방법"] != "인터넷":
        raise DamyangContractError(f"course {identity} method changed")
    schedule = _clean(f"{values['강의 요일']} {values['강의 시간']}")
    if not schedule or _normalized(schedule) not in _normalized(parent.get("raw_period")):
        raise DamyangContractError(f"course {identity} schedule mismatch")
    venue = values["교육장소"]
    if not venue:
        raise DamyangContractError(f"course {identity} venue empty")
    status_cell = None
    for th in table.find_all("th"):
        if _clean(th.get_text(" ", strip=True)) == "접수상태":
            status_cell = th.find_next_sibling("td")
            break
    if status_cell is None:
        raise DamyangContractError(f"course {identity} status cell missing")
    _validate_library_action(status_cell, identity, status_text, detail=True)
    open_now = parent.get("status") == "OPEN"
    raw_url = _clean(parent.get("raw_url"))
    row = {
        "provider": DAMYANG_LIBRARY_PROVIDER,
        "provider_course_id": f"{DAMYANG_LIBRARY_PROVIDER}:{source.code}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": DAMYANG_LIBRARY_BRANCH,
        "branch_code": _library_branch_code(),
        "provider_organizer": DAMYANG_LIBRARY_BRANCH,
        "period": f"{start} ~ {end}",
        "start_date": start,
        "end_date": end,
        "apply_period": f"{apply_start} ~ {apply_end}",
        "apply_start_date": apply_start,
        "apply_end_date": apply_end,
        "status": parent.get("status"),
        "category": "교육",
        "program_type": source.program_type,
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "교육청/도서관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": schedule,
        "target": _clean(parent.get("target")),
        "fee": "요금 별도 안내",
        "room": venue,
        "venue": venue,
        "venue_name": venue,
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
            "owner_scope": "damyang_library",
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
            "fee_evidence": "official_list_and_detail_omit_fee",
        },
    }
    _validate_output(row)
    return row


def _county_landing_contract(soup: BeautifulSoup) -> None:
    title = _one(soup.select("head > title"), "Damyang lifelong title")
    if _clean(title.get_text(" ", strip=True)) != DAMYANG_LIFELONG_BRANCH:
        raise DamyangContractError("county lifelong exact owner title changed")
    table = _one(soup.select("table.tbl.board"), "county lifelong table")
    headers = tuple(
        _clean(node.get_text(" ", strip=True))
        for node in table.select("thead.tblHeader > tr > td")
    )
    if headers != _COUNTY_LANDING_HEADERS:
        raise DamyangContractError("county lifelong headers changed")
    if table.select_one("tbody#dev-listArea") is None:
        raise DamyangContractError("county lifelong dynamic list target changed")
    paging = _one(soup.select("ul#paging-tag.pagination"), "county lifelong pager")
    if _clean(paging.get("rowcnt")) != "10" or _clean(paging.get("pagecnt")) != "5":
        raise DamyangContractError("county lifelong pager contract changed")
    scripts = "\n".join(node.get_text("\n", strip=True) for node in soup.select("script"))
    required_literals = (
        "var domainId = 'DOM_0000011'",
        "var boardId = 'BBS_0000098'",
        "var contentsSid = '264'",
        "var menuCd = 'DOM_000001101001000000'",
        "'/board/getContentsList'",
        "'/board/detail?domainId='",
        "'/board/write?domainId='",
        "'&boardType=register'",
        "ROW_CNT",
    )
    # ROW_CNT is supplied by commonFn.js rather than this document; validate
    # the local paging element above and all identity-bound route literals.
    for literal in required_literals[:-1]:
        if literal not in scripts:
            raise DamyangContractError(
                f"county lifelong application/list script changed: {literal}"
            )
    if not all(
        fragment in scripts
        for fragment in (
            "nowDate < registerStartDate",
            "registerStartDate < nowDate",
            "nowDate < registerEndDate",
            "listItem['tmpField2'] === 'P'",
        )
    ):
        raise DamyangContractError("county lifelong date-bound control changed")


def _county_api_page(
    payload: Mapping[str, Any], page: int
) -> tuple[list[dict[str, Any]], int]:
    if payload.get("RSLT_CD") != "0000" or payload.get("RSLT_MSG") != "SUCCESS":
        raise DamyangContractError("county lifelong list API result changed")
    total = payload.get("PG_TOT_CNT")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise DamyangContractError("county lifelong total changed")
    data = payload.get("RSLT_DATA")
    if not isinstance(data, Mapping):
        raise DamyangContractError("county lifelong list data missing")
    items = data.get("boardContentsList")
    if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
        raise DamyangContractError("county lifelong rows changed")
    request = payload.get("REQ_DATA")
    expected_request = {
        "domainId": DAMYANG_LIFELONG_DOMAIN_ID,
        "boardId": DAMYANG_LIFELONG_BOARD_ID,
        "startDate": "",
        "endDate": "",
        "searchCondition1": "",
        "searchCondition2": "",
        "searchKeywordCon": "",
        "searchKeyword": "",
        "ROW_CNT": str(DAMYANG_LIFELONG_PAGE_SIZE),
        "BEGIN_ROW_IDX": str((page - 1) * DAMYANG_LIFELONG_PAGE_SIZE + 1),
        "CUR_PAGE_IDX": str(page),
        "PAGING_PROCESS_STATUS": "GET_PAGED_DATA",
        "PG_TOT_CNT": total,
    }
    if request != expected_request:
        raise DamyangContractError("county lifelong API request echo changed")
    known_keys = {
        "RNUM_REVERSE",
        "dataSid",
        "cate1Nm",
        "extFeeMoney",
        "tmpField1",
        "tmpField2",
        "applicant",
        "tmpField9",
        "RNUM",
        "tmpField7",
        "tmpField8",
        "tmpField5",
        "tmpField6",
        "PG_ROW_NUM",
        "PG_TOT_CNT",
        "dataTitle",
        "extFixedNum",
    }
    result: list[dict[str, Any]] = []
    for offset, item in enumerate(items):
        if not set(item) <= known_keys:
            raise DamyangContractError("county lifelong row fields changed")
        identity = _clean(item.get("dataSid"))
        if _IDENTITY_RE.fullmatch(identity) is None:
            raise DamyangContractError("county lifelong row identity changed")
        sequence_value = item.get("PG_ROW_NUM")
        row_total = item.get("PG_TOT_CNT")
        rnum = item.get("RNUM")
        if (
            isinstance(sequence_value, bool)
            or int(sequence_value or 0) != total - ((page - 1) * DAMYANG_LIFELONG_PAGE_SIZE + offset)
            or row_total != total
            or int(rnum or 0) != (page - 1) * DAMYANG_LIFELONG_PAGE_SIZE + offset + 1
        ):
            raise DamyangContractError(f"county course {identity} numbering changed")
        title = _clean(unescape(_clean(item.get("dataTitle"))))
        # Historical titles legitimately use escaped angle-bracket labels such
        # as ``<SNS마케팅>``.  Keep the decoded visible title, but reject
        # control characters and unreasonable payloads instead of mistaking
        # those labels for HTML tags.
        if (
            not title
            or len(title) > 500
            or any(ord(character) < 32 for character in title)
        ):
            raise DamyangContractError(f"county course {identity} title changed")
        source_status = _clean(item.get("tmpField1"))
        if source_status not in {"P", "E", "D", "N"}:
            raise DamyangContractError(f"county course {identity} status changed")
        method = _clean(item.get("tmpField2"))
        if method not in {"", "P", "A"}:
            raise DamyangContractError(f"county course {identity} method changed")
        applicant = item.get("applicant")
        capacity = _clean(item.get("extFixedNum"))
        if (
            isinstance(applicant, bool)
            or not isinstance(applicant, int)
            or applicant < 0
            or (capacity and not capacity.isdigit())
        ):
            raise DamyangContractError(f"county course {identity} capacity changed")
        result.append(
            {
                "identity": identity,
                "source_sequence": int(sequence_value),
                "title": title,
                "branch": _clean(item.get("cate1Nm")),
                "source_status": source_status,
                "source_method": method,
                "apply_start": _clean(item.get("tmpField5")),
                "apply_end": _clean(item.get("tmpField6")),
                "start_date": _clean(item.get("tmpField7")),
                "end_date": _clean(item.get("tmpField8")),
                "capacity_current": applicant,
                "capacity_total": int(capacity) if capacity else None,
                "fee": _clean(item.get("extFeeMoney")),
                "raw_url": damyang_lifelong_detail_url(identity),
            }
        )
    return result, total


def _county_signature(rows: Iterable[Mapping[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    keys = (
        "identity",
        "source_sequence",
        "title",
        "branch",
        "source_status",
        "source_method",
        "apply_start",
        "apply_end",
        "start_date",
        "end_date",
        "capacity_current",
        "capacity_total",
        "fee",
    )
    return tuple(tuple(row.get(key) for key in keys) for row in rows)


def _county_status(parent: Mapping[str, Any], cutoff: date) -> str:
    if parent.get("source_status") != "P":
        return "CLOSED"
    apply_start = _safe_date(parent.get("apply_start"))
    apply_end = _safe_date(parent.get("apply_end"))
    if apply_start is None or apply_end is None or apply_end < apply_start:
        raise DamyangContractError(
            f"county course {parent.get('identity')} current application dates invalid"
        )
    if cutoff < apply_start:
        return "SCHEDULED"
    if cutoff <= apply_end:
        return "OPEN"
    return "CLOSED"


def _county_branch_code(branch: str) -> str:
    digest = hashlib.sha1(
        f"{DAMYANG_LIFELONG_PROVIDER}|{branch}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"{DAMYANG_LIFELONG_PROVIDER}:{digest}"[:100]


def _parse_county_detail(
    payload: Mapping[str, Any], parent: Mapping[str, Any], target: Any, cutoff: date
) -> dict[str, Any]:
    identity = _clean(parent.get("identity"))
    if payload.get("RSLT_CD") != "0000" or payload.get("RSLT_MSG") != "SUCCESS":
        raise DamyangContractError(f"county course {identity} detail result changed")
    if payload.get("REQ_DATA") != {
        "boardId": DAMYANG_LIFELONG_BOARD_ID,
        "dataSid": identity,
    }:
        raise DamyangContractError(f"county course {identity} detail echo changed")
    data = payload.get("RSLT_DATA")
    detail_root = data.get("boardDetail") if isinstance(data, Mapping) else None
    detail = (
        detail_root.get("boardContentsDetail")
        if isinstance(detail_root, Mapping)
        else None
    )
    files = (
        detail_root.get("boardContentsFileList")
        if isinstance(detail_root, Mapping)
        else None
    )
    if not isinstance(detail, Mapping) or not isinstance(files, list):
        raise DamyangContractError(f"county course {identity} detail schema changed")
    expected_detail_keys = {
        "cate1Nm",
        "cate2Nm",
        "categoryCode1",
        "categoryCode2",
        "dataContent",
        "dataTitle",
        "extBank",
        "extBaseTime",
        "extContact",
        "extEduTime",
        "extFeeMoney",
        "extFixedNum",
        "extInfo",
        "extPlace",
        "extReadyNum",
        "extTeacher",
        "extTel",
        "extTimeType",
        "tmpField1",
        "tmpField2",
        "tmpField4",
        "tmpField5",
        "tmpField6",
        "tmpField7",
        "tmpField8",
        "tmpField9",
    }
    if set(detail) != expected_detail_keys:
        raise DamyangContractError(f"county course {identity} detail fields changed")
    # Only these allowlisted values are read.  Free-form content, contact,
    # teacher, bank and attachment objects remain untouched.
    safe = {
        key: _clean(unescape(_clean(detail.get(key))))
        for key in (
            "dataTitle",
            "tmpField1",
            "tmpField2",
            "tmpField4",
            "tmpField5",
            "tmpField6",
            "tmpField7",
            "tmpField8",
            "cate1Nm",
            "cate2Nm",
            "extFeeMoney",
            "extPlace",
            "extFixedNum",
            "extReadyNum",
            "extEduTime",
            "extTimeType",
            "extBaseTime",
        )
    }
    for key in (
        "dataTitle",
        "tmpField1",
        "tmpField2",
        "tmpField5",
        "tmpField6",
        "tmpField7",
        "tmpField8",
        "cate1Nm",
        "extFeeMoney",
        "extFixedNum",
    ):
        list_key = {
            "dataTitle": "title",
            "tmpField1": "source_status",
            "tmpField2": "source_method",
            "tmpField5": "apply_start",
            "tmpField6": "apply_end",
            "tmpField7": "start_date",
            "tmpField8": "end_date",
            "cate1Nm": "branch",
            "extFeeMoney": "fee",
            "extFixedNum": "capacity_total",
        }[key]
        expected = parent.get(list_key)
        if key == "extFixedNum":
            expected = "" if expected is None else str(expected)
        if _normalized(safe[key]) != _normalized(expected):
            raise DamyangContractError(
                f"county course {identity} detail/list {key} mismatch"
            )
    venue = safe["extPlace"]
    branch = safe["cate1Nm"]
    if not venue or not branch:
        raise DamyangContractError(f"county course {identity} institution/venue empty")
    status = _county_status(parent, cutoff)
    online = safe["tmpField2"] == "P"
    open_online = status == "OPEN" and online
    total = int(safe["extFixedNum"])
    current = int(parent.get("capacity_current") or 0)
    wait_total = int(safe["extReadyNum"] or 0)
    application_method = "온라인" if online else "서면접수"
    raw_url = _clean(parent.get("raw_url"))
    row = {
        "provider": DAMYANG_LIFELONG_PROVIDER,
        "provider_course_id": f"{DAMYANG_LIFELONG_PROVIDER}:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": _clean(parent.get("title")),
        "branch": branch,
        "branch_code": _county_branch_code(branch),
        "provider_organizer": branch,
        "period": f"{safe['tmpField7']} ~ {safe['tmpField8']}",
        "start_date": safe["tmpField7"],
        "end_date": safe["tmpField8"],
        "apply_period": f"{safe['tmpField5']} ~ {safe['tmpField6']}",
        "apply_start_date": safe["tmpField5"],
        "apply_end_date": safe["tmpField6"],
        "status": status,
        "category": "교육",
        "program_type": safe["cate2Nm"] or "평생학습 강좌",
        "domain_category": "교육",
        "collection_category": "공공예약",
        "collection_type": "official_json_paginated_list+verified_detail",
        "source_group": "municipal_reservation",
        "operator_type": "지자체/공공기관",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "schedule_raw": _clean(f"{safe['extEduTime']}{safe['extTimeType']}"),
        "target": safe["tmpField4"],
        "fee": "무료" if safe["extFeeMoney"] in {"0", "0원"} else safe["extFeeMoney"],
        "room": venue,
        "venue": venue,
        "venue_name": venue,
        "description": _clean(parent.get("title")),
        "capacity": total,
        "capacity_current": current,
        "capacity_total": total,
        "capacity_remaining": max(0, total - current),
        "waitlist_total": wait_total,
        "application_method": application_method,
        "application_methods": [application_method],
        "reservation_available": open_online,
        "application_url": (
            damyang_lifelong_application_url(identity) if open_online else ""
        ),
        "application_type": "ONLINE_RESERVATION" if open_online else "",
        "raw_url": raw_url,
        "source_url": _clean(_target_value(target, "url")),
        "raw_fields": {
            "owner_scope": "damyang_county_lifelong",
            "source_sequence": parent.get("source_sequence"),
            "source_identity": identity,
            "source_status": safe["tmpField1"],
            "source_application_method": safe["tmpField2"],
            "list_schema_verified": True,
            "detail_schema_verified": True,
            "list_detail_verified": True,
            "capacity_verified": True,
            "application_control_verified": True,
            "fee_evidence": "official_json_extFeeMoney",
        },
    }
    _validate_output(row)
    return row


def _walk(value: Any) -> Iterable[Any]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield key
            yield from _walk(nested)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for nested in value:
            yield from _walk(nested)
    else:
        yield value


def _validate_output(row: Mapping[str, Any]) -> None:
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or not set(raw) <= _SAFE_RAW_FIELDS:
        raise DamyangContractError("persisted raw-field allowlist changed")
    strings = [value for value in _walk(row) if isinstance(value, str)]
    lowered = {_clean(value).casefold() for value in strings}
    if lowered & {value.casefold() for value in _FORBIDDEN_OUTPUT_KEYS}:
        raise DamyangContractError("forbidden detail key reached output")
    if any(_PHONE_RE.search(value) or _EMAIL_RE.search(value) for value in strings):
        raise DamyangContractError("phone/email reached output")
    if row.get("description") != row.get("title"):
        raise DamyangContractError("description must contain title only")
    if bool(row.get("application_url")) != bool(row.get("reservation_available")):
        raise DamyangContractError("application URL/availability mismatch")
    required = (
        "target",
        "fee",
        "start_date",
        "end_date",
        "category",
        "schedule_raw",
    )
    missing = [field for field in required if not _clean(row.get(field))]
    if not _clean(row.get("venue_name") or row.get("venue")):
        missing.append("venue_name")
    if missing:
        raise DamyangContractError(
            f"required output fields missing {','.join(missing)}"
        )


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta(owner: str) -> dict[str, Any]:
    return {
        "owner": owner,
        "pages": 0,
        "request_count": 0,
        "sessions_created": 0,
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
        "duplicate_count": 0,
        "source_cap_reached": False,
        "pagination_complete": False,
        "details_complete": False,
        "application_controls_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def _failure(owner: str, message: str, **updates: Any) -> dict[str, Any]:
    meta = _base_meta(owner)
    meta.update(updates)
    meta["configured_collection_error"] = message
    return meta


def collect_damyang_library_courses(
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
    if not is_damyang_library_target(target):
        return [], DAMYANG_LIBRARY_PARSER, _failure(
            "damyang_library", "target does not match primary Damyang Library owner"
        )
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        timeout_value = int(timeout)
        cutoff_at = _reference_datetime(today)
        cutoff = cutoff_at.date()
        if (
            isinstance(max_pages, bool)
            or isinstance(detail_limit, bool)
            or allowed_pages < 0
            or allowed_details < 0
            or timeout_value <= 0
        ):
            raise ValueError
    except (TypeError, ValueError):
        return [], DAMYANG_LIBRARY_PARSER, _failure(
            "damyang_library", "max_pages/detail_limit/timeout/today invalid"
        )
    client = _Client(
        timeout=timeout_value,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _library_session_factory,
    )
    errors: list[str] = []
    source_cap = False
    source_rows: dict[str, list[dict[str, Any]]] = {}
    source_totals: dict[str, int] = {}
    source_page_counts: dict[str, list[int]] = {}
    source_current_counts: dict[str, int] = {}
    source_expired_counts: dict[str, int] = {}
    source_status_counts: dict[str, dict[str, int]] = {}
    first_rows: dict[str, list[dict[str, Any]]] = {}
    data_pages: dict[str, int] = {}
    list_requests = list_rechecks = sentinels = 0
    detail_attempts = detail_pages = 0
    required_list_requests = 0
    if allowed_pages < len(DAMYANG_LIBRARY_SOURCES):
        source_cap = True
        errors.append("max_pages cannot fetch all three first pages")
    if not errors:
        for source in DAMYANG_LIBRARY_SOURCES:
            try:
                rows, empty = _parse_library_page(source, client.html(source.url), 1)
                list_requests += 1
                if rows:
                    total = int(rows[0]["source_sequence"])
                elif empty:
                    total = 0
                else:
                    raise DamyangContractError("first page lacks rows/empty marker")
                first_rows[source.code] = rows
                source_totals[source.code] = total
                data_pages[source.code] = max(
                    1, math.ceil(total / DAMYANG_LIBRARY_PAGE_SIZE)
                )
            except Exception as exc:
                errors.append(f"{source.code} first page: {type(exc).__name__}: {exc}")
                break
    if not errors:
        required_list_requests = sum(pages + 2 for pages in data_pages.values())
        if required_list_requests > allowed_pages:
            source_cap = True
            errors.append(
                f"max_pages allows {allowed_pages} of {required_list_requests} required"
            )
    if not errors:
        for source in DAMYANG_LIBRARY_SOURCES:
            total = source_totals[source.code]
            pages = data_pages[source.code]
            collected: list[dict[str, Any]] = []
            counts: list[int] = []
            try:
                for page in range(1, pages + 1):
                    if page == 1:
                        rows = first_rows[source.code]
                    else:
                        rows, empty = _parse_library_page(
                            source,
                            client.html(damyang_library_list_url(source.code, page)),
                            page,
                        )
                        list_requests += 1
                        if empty:
                            raise DamyangContractError("data page unexpectedly empty")
                    expected = (
                        min(
                            DAMYANG_LIBRARY_PAGE_SIZE,
                            total - (page - 1) * DAMYANG_LIBRARY_PAGE_SIZE,
                        )
                        if total
                        else 0
                    )
                    if len(rows) != expected:
                        raise DamyangContractError(
                            f"page {page} expected {expected}, got {len(rows)}"
                        )
                    counts.append(len(rows))
                    collected.extend(rows)
                sentinel_page = pages + 1
                sentinel_rows, empty = _parse_library_page(
                    source,
                    client.html(damyang_library_list_url(source.code, sentinel_page)),
                    sentinel_page,
                )
                list_requests += 1
                if sentinel_rows or not empty:
                    raise DamyangContractError("immediate sentinel not explicitly empty")
                sentinels += 1
                rechecked, recheck_empty = _parse_library_page(
                    source, client.html(source.url), 1
                )
                list_requests += 1
                list_rechecks += 1
                if recheck_empty != (not first_rows[source.code]) or _library_signature(
                    rechecked
                ) != _library_signature(first_rows[source.code]):
                    raise DamyangContractError("page-one recheck changed")
                numbers = [row["source_sequence"] for row in collected]
                identities = [row["identity"] for row in collected]
                if numbers != list(range(total, 0, -1)) or len(collected) != total:
                    raise DamyangContractError("source numbering/total incomplete")
                if len(identities) != len(set(identities)):
                    raise DamyangContractError("duplicate source identities")
                source_rows[source.code] = collected
                source_page_counts[source.code] = counts
            except Exception as exc:
                errors.append(f"{source.code} completeness: {type(exc).__name__}: {exc}")
                break
    all_rows = [
        row
        for source in DAMYANG_LIBRARY_SOURCES
        for row in source_rows.get(source.code, [])
    ]
    identity_sources: dict[str, set[str]] = {}
    for row in all_rows:
        identity_sources.setdefault(row["identity"], set()).add(row["source_catalogue"])
    overlap = sum(len(scopes) - 1 for scopes in identity_sources.values() if len(scopes) > 1)
    if overlap:
        errors.append(f"{overlap} identities overlap across library scopes")
    current: list[dict[str, Any]] = []
    expired = 0
    for source in DAMYANG_LIBRARY_SOURCES:
        statuses: Counter[str] = Counter()
        current_count = expired_count = 0
        for row in source_rows.get(source.code, []):
            statuses[row["source_status"]] += 1
            end = date.fromisoformat(row["end_date"])
            if end < cutoff:
                expired += 1
                expired_count += 1
            else:
                apply_start_at = _safe_datetime(row.get("apply_start_at"))
                apply_end_at = _safe_datetime(row.get("apply_end_at"))
                if apply_start_at is None or apply_end_at is None:
                    errors.append(
                        f"course {row['identity']} application datetime missing"
                    )
                elif (
                    row["status"] == "OPEN"
                    and not apply_start_at <= cutoff_at <= apply_end_at
                ):
                    errors.append(
                        f"course {row['identity']} open outside application datetime"
                    )
                elif (
                    row["status"] == "SCHEDULED"
                    and not cutoff_at < apply_start_at
                ):
                    errors.append(
                        f"course {row['identity']} scheduled datetime mismatch"
                    )
                current.append(row)
                current_count += 1
        source_current_counts[source.code] = current_count
        source_expired_counts[source.code] = expired_count
        source_status_counts[source.code] = dict(statuses)
    if len(current) > allowed_details:
        source_cap = True
        errors.append(f"detail_limit allows {allowed_details} of {len(current)}")
    detailed: list[dict[str, Any]] = []
    if not errors:
        for parent in current:
            detail_attempts += 1
            source = _LIBRARY_SOURCE_BY_CODE[parent["source_catalogue"]]
            try:
                detailed.append(
                    _parse_library_detail(
                        source, parent, client.html(parent["raw_url"]), target
                    )
                )
                detail_pages += 1
            except Exception as exc:
                errors.append(
                    f"course {parent['identity']} detail: {type(exc).__name__}: {exc}"
                )
                break
    result: list[dict[str, Any]] = []
    if not errors:
        result = list((dedupe_rows or _dedupe_default)(detailed))
        if len(result) != len(detailed):
            errors.append("dedupe changed complete library row count")
            result = []
    result.sort(key=lambda row: (row["start_date"], row["title"], row["provider_course_id"]))
    duplicates = len(detailed) - len({row["provider_course_id"] for row in detailed})
    if duplicates and not errors:
        errors.append(f"{duplicates} duplicate output identities")
        result = []
    snapshot = not errors
    pagination_complete = bool(
        snapshot
        and list_requests == required_list_requests
        and sentinels == len(DAMYANG_LIBRARY_SOURCES)
        and list_rechecks == len(DAMYANG_LIBRARY_SOURCES)
        and len(all_rows) == sum(source_totals.values())
    )
    details_complete = bool(
        snapshot and detail_attempts == len(current) and detail_pages == len(current)
    )
    controls_complete = bool(
        details_complete
        and all(
            row["raw_fields"].get("application_control_verified")
            and row["raw_fields"].get("login_gate_verified")
            for row in detailed
        )
    )
    meta = _base_meta("damyang_library")
    meta.update(
        {
            "pages": client.requests,
            "request_count": client.requests,
            "sessions_created": client.sessions_created,
            "source_count": len(DAMYANG_LIBRARY_SOURCES),
            "source_totals": source_totals,
            "source_page_counts": source_page_counts,
            "source_current_counts": source_current_counts,
            "source_expired_counts": source_expired_counts,
            "source_status_counts": source_status_counts,
            "source_rows": len(all_rows),
            "current_count": len(current),
            "expired_count": expired,
            "returned_count": len(result),
            "required_list_requests": required_list_requests,
            "list_requests": list_requests,
            "list_rechecks": list_rechecks,
            "sentinel_pages": sentinels,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "cross_source_duplicate_count": overlap,
            "duplicate_count": duplicates,
            "source_cap_reached": source_cap,
            "pagination_complete": pagination_complete,
            "partition_union_complete": bool(snapshot and not overlap and len(source_rows) == 3),
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot,
            "full_snapshot_validated": bool(
                snapshot and pagination_complete and details_complete and controls_complete
            ),
            "no_current_data": bool(snapshot and not current),
            "no_current_reason": (
                "all rows in all three complete Damyang Library catalogues ended"
                if snapshot and not current
                else ""
            ),
            "exact_branch_name": DAMYANG_LIBRARY_BRANCH,
            "configured_collection_error": "; ".join(errors),
        }
    )
    return ([] if errors else result), DAMYANG_LIBRARY_PARSER, meta


def collect_damyang_lifelong_courses(
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
    if not is_damyang_lifelong_target(target):
        return [], DAMYANG_LIFELONG_PARSER, _failure(
            "damyang_county_lifelong",
            "target does not match canonical Damyang Lifelong Learning owner",
        )
    try:
        allowed_pages = int(max_pages)
        allowed_details = int(detail_limit)
        timeout_value = int(timeout)
        cutoff = _today(today)
        if (
            isinstance(max_pages, bool)
            or isinstance(detail_limit, bool)
            or allowed_pages < 0
            or allowed_details < 0
            or timeout_value <= 0
        ):
            raise ValueError
    except (TypeError, ValueError):
        return [], DAMYANG_LIFELONG_PARSER, _failure(
            "damyang_county_lifelong", "max_pages/detail_limit/timeout/today invalid"
        )
    client = _Client(
        timeout=timeout_value,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _county_session_factory,
    )
    errors: list[str] = []
    source_cap = False
    list_requests = list_rechecks = sentinels = 0
    detail_attempts = detail_pages = 0
    page_counts: list[int] = []
    total = 0
    required_list_requests = 0
    first_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    try:
        _county_landing_contract(client.html(DAMYANG_LIFELONG_URL))
        first_rows, total = _county_api_page(client.json(damyang_lifelong_api_url(1)), 1)
        list_requests += 1
    except Exception as exc:
        errors.append(f"landing/first page: {type(exc).__name__}: {exc}")
    data_pages = max(1, math.ceil(total / DAMYANG_LIFELONG_PAGE_SIZE)) if not errors else 0
    required_list_requests = data_pages + 2 if data_pages else 0
    # max_pages counts landing plus list data/sentinel/recheck, matching actual
    # network work while detail_limit controls detail calls separately.
    if not errors and required_list_requests + 1 > allowed_pages:
        source_cap = True
        errors.append(
            f"max_pages allows {allowed_pages} of {required_list_requests + 1} required"
        )
    if not errors:
        try:
            for page in range(1, data_pages + 1):
                if page == 1:
                    rows = first_rows
                else:
                    rows, page_total = _county_api_page(
                        client.json(damyang_lifelong_api_url(page)), page
                    )
                    list_requests += 1
                    if page_total != total:
                        raise DamyangContractError("county total changed between pages")
                expected = min(
                    DAMYANG_LIFELONG_PAGE_SIZE,
                    max(0, total - (page - 1) * DAMYANG_LIFELONG_PAGE_SIZE),
                )
                if len(rows) != expected:
                    raise DamyangContractError(
                        f"county page {page} expected {expected}, got {len(rows)}"
                    )
                page_counts.append(len(rows))
                all_rows.extend(rows)
            sentinel_rows, sentinel_total = _county_api_page(
                client.json(damyang_lifelong_api_url(data_pages + 1)), data_pages + 1
            )
            list_requests += 1
            if sentinel_total != total or sentinel_rows:
                raise DamyangContractError("county immediate sentinel not empty")
            sentinels += 1
            rechecked, recheck_total = _county_api_page(
                client.json(damyang_lifelong_api_url(1)), 1
            )
            list_requests += 1
            list_rechecks += 1
            if recheck_total != total or _county_signature(rechecked) != _county_signature(first_rows):
                raise DamyangContractError("county page-one recheck changed")
            numbers = [row["source_sequence"] for row in all_rows]
            identities = [row["identity"] for row in all_rows]
            if numbers != list(range(total, 0, -1)) or len(all_rows) != total:
                raise DamyangContractError("county numbering/total incomplete")
            if len(identities) != len(set(identities)):
                raise DamyangContractError("county duplicate identities")
        except Exception as exc:
            errors.append(f"completeness: {type(exc).__name__}: {exc}")
    current: list[dict[str, Any]] = []
    expired = 0
    historical_incomplete = 0
    source_status_counts: Counter[str] = Counter()
    source_method_counts: Counter[str] = Counter()
    for row in all_rows:
        source_status_counts[row["source_status"]] += 1
        source_method_counts[row["source_method"]] += 1
        end = _safe_date(row.get("end_date"))
        if end is None:
            known_dates = [
                value
                for value in (
                    _safe_date(row.get("apply_start")),
                    _safe_date(row.get("apply_end")),
                    _safe_date(row.get("start_date")),
                )
                if value is not None
            ]
            if known_dates and max(known_dates) < cutoff:
                historical_incomplete += 1
                expired += 1
                continue
            errors.append(f"county course {row['identity']} has ambiguous current end date")
            continue
        if end < cutoff:
            expired += 1
        else:
            if (
                _safe_date(row.get("start_date")) is None
                or _safe_date(row.get("apply_start")) is None
                or _safe_date(row.get("apply_end")) is None
                or not row.get("branch")
                or row.get("capacity_total") is None
            ):
                errors.append(f"county course {row['identity']} current fields incomplete")
            else:
                try:
                    row["status"] = _county_status(row, cutoff)
                    current.append(row)
                except Exception as exc:
                    errors.append(f"county course {row['identity']} status: {exc}")
    if len(current) > allowed_details:
        source_cap = True
        errors.append(f"detail_limit allows {allowed_details} of {len(current)}")
    detailed: list[dict[str, Any]] = []
    if not errors:
        for parent in current:
            detail_attempts += 1
            try:
                detailed.append(
                    _parse_county_detail(
                        client.json(damyang_lifelong_detail_api_url(parent["identity"])),
                        parent,
                        target,
                        cutoff,
                    )
                )
                detail_pages += 1
            except Exception as exc:
                errors.append(
                    f"county course {parent['identity']} detail: {type(exc).__name__}: {exc}"
                )
                break
    result: list[dict[str, Any]] = []
    if not errors:
        result = list((dedupe_rows or _dedupe_default)(detailed))
        if len(result) != len(detailed):
            errors.append("dedupe changed complete county row count")
            result = []
    result.sort(key=lambda row: (row["start_date"], row["title"], row["provider_course_id"]))
    duplicates = len(detailed) - len({row["provider_course_id"] for row in detailed})
    if duplicates and not errors:
        errors.append(f"{duplicates} duplicate output identities")
        result = []
    snapshot = not errors
    pagination_complete = bool(
        snapshot
        and list_requests == required_list_requests
        and sentinels == 1
        and list_rechecks == 1
        and len(all_rows) == total
    )
    details_complete = bool(
        snapshot and detail_attempts == len(current) and detail_pages == len(current)
    )
    controls_complete = bool(
        details_complete
        and all(row["raw_fields"].get("application_control_verified") for row in detailed)
    )
    meta = _base_meta("damyang_county_lifelong")
    meta.update(
        {
            "pages": client.requests,
            "request_count": client.requests,
            "sessions_created": client.sessions_created,
            "landing_requests": 1 if client.requests else 0,
            "source_total": total,
            "source_page_counts": page_counts,
            "source_status_counts": dict(source_status_counts),
            "source_method_counts": dict(source_method_counts),
            "historical_incomplete_date_count": historical_incomplete,
            "source_rows": len(all_rows),
            "current_count": len(current),
            "expired_count": expired,
            "returned_count": len(result),
            "required_list_requests": required_list_requests,
            "required_total_page_requests": required_list_requests + 1,
            "list_requests": list_requests,
            "list_rechecks": list_rechecks,
            "sentinel_pages": sentinels,
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "duplicate_count": duplicates,
            "source_cap_reached": source_cap,
            "pagination_complete": pagination_complete,
            "details_complete": details_complete,
            "application_controls_complete": controls_complete,
            "snapshot_complete": snapshot,
            "full_snapshot_validated": bool(
                snapshot and pagination_complete and details_complete and controls_complete
            ),
            "no_current_data": bool(snapshot and not current),
            "no_current_reason": (
                "all rows in the complete Damyang Lifelong catalogue ended"
                if snapshot and not current
                else ""
            ),
            "exact_branch_name": DAMYANG_LIFELONG_BRANCH,
            "exact_current_branches": dict(Counter(row["branch"] for row in detailed)),
            "configured_collection_error": "; ".join(errors),
        }
    )
    return ([] if errors else result), DAMYANG_LIFELONG_PARSER, meta


def collect_damyang_education(
    target: Any,
    timeout: int = 20,
    max_pages: int = 20,
    detail_limit: int = 100,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if is_damyang_library_target(target):
        return collect_damyang_library_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    if is_damyang_lifelong_target(target):
        return collect_damyang_lifelong_courses(
            target, timeout, max_pages, detail_limit, **kwargs
        )
    return [], DAMYANG_PARSER, _failure(
        "damyang_dispatch", "target is not either audited Damyang education owner"
    )


collect = collect_damyang_education


__all__ = [
    "DAMYANG_BOSEONG_CANONICAL_URL",
    "DAMYANG_BOSEONG_PROVIDER",
    "DAMYANG_CANDIDATE_AUDIT",
    "DAMYANG_CROSS_HOST_A804_PROVIDER",
    "DAMYANG_CROSS_HOST_A804_URL",
    "DAMYANG_DISCOVERY_AUDIT",
    "DAMYANG_LIBRARY_BRANCH",
    "DAMYANG_LIBRARY_PARSER",
    "DAMYANG_LIBRARY_PROVIDER",
    "DAMYANG_LIBRARY_SOURCES",
    "DAMYANG_LIBRARY_URL",
    "DAMYANG_LIFELONG_BRANCH",
    "DAMYANG_LIFELONG_PARSER",
    "DAMYANG_LIFELONG_PROVIDER",
    "DAMYANG_LIFELONG_URL",
    "DAMYANG_MUNICIPALITY_CODE",
    "DAMYANG_MUNICIPALITY_NAME",
    "DAMYANG_OWNER_BOUNDARY_AUDIT",
    "DAMYANG_PARSER",
    "DAMYANG_PII_FIELDS_DISCARDED",
    "DAMYANG_READING_PROVIDER",
    "DAMYANG_READING_URL",
    "DAMYANG_HUMANITIES_PROVIDER",
    "DAMYANG_HUMANITIES_URL",
    "DamyangContractError",
    "DamyangLibrarySource",
    "DamyangLibraryTlsAdapter",
    "build_damyang_library_tls_context",
    "collect",
    "collect_damyang_education",
    "collect_damyang_library_courses",
    "collect_damyang_lifelong_courses",
    "damyang_library_detail_url",
    "damyang_library_list_url",
    "damyang_lifelong_api_url",
    "damyang_lifelong_application_url",
    "damyang_lifelong_detail_api_url",
    "damyang_lifelong_detail_url",
    "is_damyang_library_target",
    "is_damyang_lifelong_target",
    "is_damyang_target",
    "is_target",
]
