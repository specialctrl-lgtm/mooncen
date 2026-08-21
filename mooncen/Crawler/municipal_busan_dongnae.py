"""Atomic education collector for Busan Dongnae-gu's public ledgers.

The dedicated Dongnae lifelong-learning catalogue, the Dongnae resident-
council partition of Busan's integrated reservation site, and office
``OFFICE_00002682`` on the Busan Lifelong Learning Platform form one logical
municipal snapshot.  The platform is a federation: external ``docNo`` rows
are republications of the dedicated catalogue, while native ``LEARNING_*``
rows are independent courses.  Only the latter are published by this owner.

Every declared page, the immediate post-final sentinel, stable boundary
pages (or two equal complete platform censuses), and every current/future
detail are mandatory.  Any changed identity, owner, pagination, safe detail
schema, or request cap discards the complete union.  Applicant forms/lists,
login pages, contact/instructor values, attachments, and free-form detail
payloads are never fetched or persisted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong


BUSAN_DONGNAE_PROVIDER = "MUNI_WWW_DONGNAE_GO_KR_742D8C71"
BUSAN_DONGNAE_CANDIDATE_ID = "MUNI_IR_30764A234E6F"
BUSAN_DONGNAE_ALIAS_PROVIDER = "MUNI_WWW_DONGNAE_GO_KR_23A4EF57"
BUSAN_DONGNAE_ALIAS_CANDIDATE_ID = "MUNI_IR_64D07F60A476"
BUSAN_CITY_DONGNAE_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_F0A5AD17"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_DONGNAE_MUNICIPALITY_CODE = "2626000000"
BUSAN_DONGNAE_MUNICIPALITY_NAME = "부산광역시 동래구"
BUSAN_DONGNAE_HOST = "www.dongnae.go.kr"
BUSAN_DONGNAE_PATH = "/lll/index.dongnae"
BUSAN_DONGNAE_MENU = "DOM_000000707002000000"
BUSAN_DONGNAE_DETAIL_MENU = "DOM_000000707002003000"
BUSAN_DONGNAE_URL = (
    f"https://{BUSAN_DONGNAE_HOST}{BUSAN_DONGNAE_PATH}?"
    + urlencode({"menuCd": BUSAN_DONGNAE_MENU})
)
BUSAN_DONGNAE_CANONICAL_URL = BUSAN_DONGNAE_URL
BUSAN_DONGNAE_ALIAS_URL = (
    "https://www.dongnae.go.kr/culture/index.dongnae?"
    "menuCd=DOM_000000707002000000&searchType=center"
)
BUSAN_DONGNAE_PRIVATE_HISTORY_PATH = "/lll/index.dongnae"

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_DONGNAE_GUGUN = "6"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_DONGNAE_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_DONGNAE_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_LIFELONG_DONGNAE_OFFICE = "OFFICE_00002682"
BUSAN_LIFELONG_DONGNAE_OFFICE_NAME = "동래구청"
BUSAN_LIFELONG_PAGE_SIZE = 1000
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_OFFICE_URL = _lifelong.BUSAN_LIFELONG_URL

BUSAN_DONGNAE_FETCH_ATTEMPTS = 3
BUSAN_DONGNAE_MAX_WORKERS = 12
BUSAN_DONGNAE_MAX_HTML_BYTES = 8_000_000
BUSAN_DONGNAE_PARSER = (
    "dongnae_lll_docno_all_pages+empty_sentinel+stable_first_last+"
    "busan_lifelong_office00002682_pageunit1000_two_complete_censuses+"
    "external_docno_duplicate_suppression+audited_training_test_exclusion+"
    "busan_reserve_gugun6_office33_all_pages+empty_sentinel+stable_first_last+"
    "all_current_safe_details+identity_bound_login_controls+pii_never_read+"
    "atomic_three_ledger_snapshot"
)
BUSAN_DONGNAE_OWNERSHIP_SCOPE = (
    "dongnae_complete_lifelong_catalogue_native_platform_courses_and_exact_"
    "busan_city_dongnae_resident_council_education"
)

BUSAN_DONGNAE_CANDIDATE_IDS: Mapping[str, str] = {
    "canonical_complete_catalogue": BUSAN_DONGNAE_CANDIDATE_ID,
    "culture_path_alias": BUSAN_DONGNAE_ALIAS_CANDIDATE_ID,
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "busan_city_single_museum_record": "MUNI_IR_2BA97ED12CEB",
    "busan_city_single_welfare_record": "MUNI_IR_5608F8475923",
    "education_support_notice_board": "MUNI_IR_8A400C4D0DE1",
    "busan_resident_councils": "MUNI_IR_6E08DDCBB806",
}

BUSAN_DONGNAE_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_DONGNAE_PROVIDER: {
        "decision": "canonical_complete_district_education_owner",
        "candidate_id": BUSAN_DONGNAE_CANDIDATE_ID,
        "url": BUSAN_DONGNAE_URL,
        "owner": BUSAN_DONGNAE_MUNICIPALITY_NAME,
    },
    BUSAN_DONGNAE_ALIAS_PROVIDER: {
        "decision": "duplicate_alias_of_canonical_dongnae_ledger",
        "candidate_id": BUSAN_DONGNAE_ALIAS_CANDIDATE_ID,
        "url": BUSAN_DONGNAE_ALIAS_URL,
        "canonical_url": BUSAN_DONGNAE_URL,
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "collect_native_learning_ids_suppress_external_docno_duplicates",
        "candidate_id": BUSAN_DONGNAE_CANDIDATE_IDS["busan_lifelong_federation"],
        "office_code": BUSAN_LIFELONG_DONGNAE_OFFICE,
        "identity_rule": "external docNo must exist in canonical district census",
    },
    BUSAN_CITY_DONGNAE_PROVIDER: {
        "decision": "collect_exact_dongnae_resident_council_partition",
        "candidate_id": BUSAN_DONGNAE_CANDIDATE_IDS["busan_resident_councils"],
        "url": BUSAN_CITY_DONGNAE_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_DONGNAE_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "EDUCATION_SUPPORT_NOTICE_BOARD": {
        "decision": "exclude_notice_board_not_a_structured_municipal_course_ledger",
        "candidate_id": BUSAN_DONGNAE_CANDIDATE_IDS[
            "education_support_notice_board"
        ],
    },
    "SINGLE_BUSAN_DETAIL_OCCURRENCES": {
        "decision": "exclude_single_record_subsets_owned_by_exact_city_partition",
        "candidate_ids": (
            BUSAN_DONGNAE_CANDIDATE_IDS["busan_city_single_museum_record"],
            BUSAN_DONGNAE_CANDIDATE_IDS["busan_city_single_welfare_record"],
        ),
    },
    "APPLICANT_AND_ACCOUNT_BOUNDARY": {
        "decision": "never_fetch_or_persist",
        "reason": "application forms, applicant lists, login/account pages contain PII",
    },
}

BUSAN_DONGNAE_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_DONGNAE_URL,
    "canonical_rows": 224,
    "canonical_data_pages": 9,
    "canonical_page_counts": {"1-8": 25, "9": 24},
    "canonical_sentinel_page": 10,
    "canonical_sentinel_mode": "empty",
    "canonical_current_rows": 25,
    "canonical_current_status_counts": {"접수중": 1, "마감": 24},
    "canonical_closed_stale_login_control_rows": 8,
    "canonical_closed_stale_login_control_rule": (
        "list status is 마감, list/detail application dates agree, apply_end is "
        "not after the crawl date, and the sole detail control is exact goLogin W/Y; "
        "always suppress the control and never emit an application URL"
    ),
    "lifelong_office": BUSAN_LIFELONG_DONGNAE_OFFICE,
    "lifelong_rows": 232,
    "lifelong_collector_page_size": BUSAN_LIFELONG_PAGE_SIZE,
    "lifelong_data_pages": 1,
    "lifelong_sentinel_page": 2,
    "lifelong_external_rows": 222,
    "lifelong_external_unique_docno": 222,
    "lifelong_external_rows_matching_canonical_docno": 222,
    "lifelong_external_repeated_rows": 0,
    "lifelong_canonical_rows_not_yet_republished": 2,
    "lifelong_native_rows": 10,
    "lifelong_native_current_rows": 10,
    "lifelong_audited_training_test_rows": 1,
    "lifelong_native_publishable_current_rows": 9,
    "resident_url": BUSAN_CITY_DONGNAE_URL,
    "resident_rows": 83,
    "resident_data_pages": 9,
    "resident_page_counts": {"1-8": 10, "9": 3},
    "resident_sentinel_page": 10,
    "resident_current_rows": 83,
    "resident_current_status_counts": {"접수중": 23, "접수마감": 60},
    "resident_branch_count": 8,
    "resident_title_attribute_anomaly_rows": 1,
    "resident_method_separator_artifact_rows": 2,
    "source_rows": 539,
    "duplicate_external_rows": 222,
    "unique_education_source_rows": 316,
    "atomic_current_rows": 117,
    "atomic_status_counts": {"OPEN": 30, "CLOSED": 87},
    "active_online_application_rows": 7,
    "required_list_requests": 28,
    "required_detail_requests": 117,
    "complete_network_requests": 145,
    "conclusion": (
        "collect the complete district catalogue, native non-test platform rows, "
        "and the exact resident-council partition; suppress all identity-proved "
        "external platform republications"
    ),
}

_AUDITED_PLATFORM_TRAINING_TEST: Mapping[str, str] = {
    "identity": "LEARNING_00087443",
    "title": "테스트 강좌 (신청하기 연습)",
    "start_date": "2026-12-01",
    "end_date": "2026-12-31",
    "source_status": "접수중",
}
_AUDITED_CITY_TITLE_ATTRIBUTE: Mapping[str, tuple[str, str]] = {
    "192:1524": ("[권역]영어회화(초급)", "영어회화(초급)"),
}
_AUDITED_CITY_METHOD_ARTIFACTS: Mapping[str, tuple[str, str]] = {
    "192:1520": (
        "온라인, , 방문접수, , 전화접수(선착순)",
        "온라인, 방문접수, 전화접수(선착순)",
    ),
    "193:25011": (
        "방문접수, , 전화접수(접수 후 개별 통보)",
        "방문접수, 전화접수(접수 후 개별 통보)",
    ),
}
_LOCAL_LOGIN_ACTIONS = frozenset(
    {"javascript:goLogin('W');", "javascript:goLogin('Y');"}
)
_LOCAL_CLOSED_ALERT_ACTION = "javascript:alert('본 강좌는 접수 마감 되었습니다.');"


class BusanDongnaeContractError(ValueError):
    """Raised when one of the audited Dongnae source contracts changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or status-200 error-page failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_DOCNO_RE = re.compile(r"^20\d{12}$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_LOCAL_TOTAL_RE = re.compile(
    r"총게시물\s*:\s*([\d,]+)\s*,?\s*페이지\s*:\s*(\d+)\s*/\s*(\d+)"
)
_LOCAL_ONCLICK_RE = re.compile(r"^location\.href=['\"]([^'\"]+)['\"];?$")
_LOCAL_DETAIL_QUERY_RE = re.compile(
    rf"^menuCd={BUSAN_DONGNAE_DETAIL_MENU}&docNo=(20\d{{12}})&title=.*$"
)
_CITY_ACTION_RE = re.compile(
    r"^fn_viewProgrm\(\s*['\"]([1-9]\d*)['\"]\s*,\s*"
    r"['\"]([1-9]\d*)['\"]\s*\);\s*return\s+false;?$"
)
_CITY_DATES_RE = re.compile(
    r"^\[신청\]\s*(20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*\[행사\]\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[-\s]?)?(?:0\d{1,3}[-\s]?)?"
    r"\d{3,4}[-\s]\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LOCAL_HEADERS = (
    "모집",
    "강좌명",
    "접수기간",
    "강좌기간",
    "교육시간",
    "교육장소",
    "신청/정원 (대기신청 / 대기정원)",
)
_LOCAL_STATUS_MAP = {"접수중": "OPEN", "마감": "CLOSED", "접수예정": "SCHEDULED"}
_LOCAL_DETAIL_LABELS = (
    "강좌명",
    "교육기간",
    "접수기간",
    "접수시작시간",
    "접수종료시간",
    "교육요일",
    "교육시간",
    "교육신청자",
    "대기자",
    "교육장소",
    "교육문의전화",
    "강사명",
    "수강료",
    "교육내용",
    "강좌상세정보URL",
    "첨부파일",
)
_LOCAL_DETAIL_SAFE_LABELS = frozenset(
    {
        "강좌명",
        "교육기간",
        "접수기간",
        "접수시작시간",
        "접수종료시간",
        "교육요일",
        "교육시간",
        "교육장소",
        "수강료",
    }
)

_PLATFORM_DETAIL_REQUIRED = (
    "회차명",
    "강좌분류",
    "교육대상",
    "문의전화",
    "교육장소",
    "총 교육시간",
    "교육기간",
    "교육시간",
    "수강료",
    "재료비",
    "접수인원",
    "우선모집기간",
    "일반모집기간",
    "모집방법",
    "신청상태",
    "교육상태",
    "강좌소개",
    "강좌소개 첨부파일",
    "강사",
    "강의계획서",
    "결제방법",
    "주의사항",
    "검색키워드",
    "강좌제한",
)
_PLATFORM_OPTIONAL_LABELS = frozenset({"수강료 기타", "직장인 여부"})
_PLATFORM_SAFE_LABELS = frozenset(
    {
        "강좌분류",
        "교육대상",
        "교육장소",
        "총 교육시간",
        "교육기간",
        "교육시간",
        "수강료",
        "재료비",
        "수강료 기타",
        "우선모집기간",
        "일반모집기간",
        "모집방법",
        "신청상태",
        "교육상태",
        "결제방법",
    }
)

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_CITY_DETAIL_REQUIRED = (
    "운영기간",
    "신청기간",
    "취소여부",
    "신청방법",
    "수강료",
    "요일 /시간",
    "문의전화",
    "운영기관",
    "대상",
)
_CITY_DETAIL_SAFE_LABELS = frozenset(_CITY_DETAIL_REQUIRED) - {"문의전화"}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanDongnaeContractError(f"expected one {label}, found {len(found)}")
    return found[0]


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is not None:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanDongnaeContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanDongnaeContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanDongnaeContractError(f"{label} must be positive")
    return result


def is_busan_dongnae_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_DONGNAE_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == BUSAN_DONGNAE_HOST
        and parsed.port is None
        and parsed.path == BUSAN_DONGNAE_PATH
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
        and query == {"menuCd": [BUSAN_DONGNAE_MENU]}
    )


is_target = is_busan_dongnae_education_target


def busan_dongnae_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    query: list[tuple[str, Any]] = [("menuCd", BUSAN_DONGNAE_MENU)]
    if value != 1:
        query.append(("pageno", value))
    return f"https://{BUSAN_DONGNAE_HOST}{BUSAN_DONGNAE_PATH}?" + urlencode(query)


def busan_dongnae_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _DOCNO_RE.fullmatch(value):
        raise BusanDongnaeContractError("invalid Dongnae docNo")
    return f"https://{BUSAN_DONGNAE_HOST}{BUSAN_DONGNAE_PATH}?" + urlencode(
        (("menuCd", BUSAN_DONGNAE_DETAIL_MENU), ("docNo", value))
    )


def busan_dongnae_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", value),
            ("srchGugun", BUSAN_CITY_DONGNAE_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_dongnae_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanDongnaeContractError("invalid Busan city course identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def busan_dongnae_lifelong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_DONGNAE_OFFICE, value)
    payload["pageUnit"] = str(BUSAN_LIFELONG_PAGE_SIZE)
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_dongnae_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanDongnaeContractError("invalid lifelong course identity")
    return _lifelong.busan_lifelong_detail_url(value)


def canonical_busan_dongnae_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_DONGNAE_HOST
        or parsed.port is not None
        or parsed.path != BUSAN_DONGNAE_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) not in ({"menuCd", "docNo"}, {"menuCd", "docNo", "title"})
        or query.get("menuCd") != [BUSAN_DONGNAE_DETAIL_MENU]
        or len(query.get("docNo", [])) != 1
        or not _DOCNO_RE.fullmatch(query["docNo"][0])
    ):
        return ""
    return "docno:" + query["docNo"][0]


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            if self.count >= self.limit:
                raise BusanDongnaeContractError("max_requests cap reached")
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise _TransientFetchError(f"HTTP {status} for {requested_url}")
    content = getattr(response, "content", b"")
    text = getattr(response, "text", "")
    if isinstance(content, bytes) and len(content) > BUSAN_DONGNAE_MAX_HTML_BYTES:
        raise BusanDongnaeContractError("HTML response exceeds safety limit")
    if not isinstance(text, str) or not text.strip():
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
    if not text.strip():
        raise _TransientFetchError("empty HTML response")
    soup = BeautifulSoup(text, "html.parser")
    title = _text(soup.title).casefold()
    if not soup.html or not title or any(
        token in title for token in ("temporary error", "service unavailable", "오류")
    ):
        raise _TransientFetchError("status-200 error page")
    return soup, _clean(getattr(response, "url", "")) or requested_url


@dataclass(frozen=True)
class _FetchResult:
    value: Any
    retries: int
    sessions: int


def _fetch_parsed(
    url: str,
    parser: Callable[[BeautifulSoup, str], Any],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _FetchResult:
    last: Optional[BaseException] = None
    sessions = 0
    for attempt in range(BUSAN_DONGNAE_FETCH_ATTEMPTS):
        session = session_factory()
        sessions += 1
        try:
            budget.take()
            response = fetcher(session, url, timeout)
            soup, final_url = _response_soup(response, url)
            return _FetchResult(parser(soup, final_url), attempt, sessions)
        except BusanDongnaeContractError:
            raise
        except Exception as exc:
            last = exc
            if attempt + 1 < BUSAN_DONGNAE_FETCH_ATTEMPTS:
                sleeper(min(0.15 * (2**attempt), 0.5))
        finally:
            _close_quietly(session)
    raise BusanDongnaeContractError(f"fetch failed for {url}: {_clean(last)}")


def _fetch_many(
    items: Sequence[tuple[Any, str, Callable[[BeautifulSoup, str], Any]]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
    max_workers: int,
) -> tuple[dict[Any, Any], int, int]:
    values: dict[Any, Any] = {}
    retries = 0
    sessions = 0

    def run(item: tuple[Any, str, Callable[[BeautifulSoup, str], Any]]):
        key, url, parser = item
        return key, _fetch_parsed(
            url,
            parser,
            fetcher=fetcher,
            session_factory=session_factory,
            timeout=timeout,
            sleeper=sleeper,
            budget=budget,
        )

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(items)))) as pool:
        futures = [pool.submit(run, item) for item in items]
        for future in as_completed(futures):
            key, result = future.result()
            values[key] = result.value
            retries += result.retries
            sessions += result.sessions
    return values, retries, sessions


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(date(*(int(part) for part in match.groups())))
        except ValueError as exc:
            raise BusanDongnaeContractError("invalid source date") from exc
    return result


def _date_pair(value: Any, label: str) -> tuple[str, str]:
    found = _dates(value)
    if len(found) != 2 or found[1] < found[0]:
        raise BusanDongnaeContractError(f"{label} changed")
    return found[0].isoformat(), found[1].isoformat()


def _local_application_decision(
    *,
    source_status: str,
    apply_start: str,
    apply_end: str,
    cutoff: date,
    control_actions: Sequence[str],
) -> tuple[bool, bool, str]:
    """Classify a verified detail control without exposing its target.

    Dongnae keeps a JavaScript login button on some rows after the list has
    changed to ``마감``.  That is safe to suppress only when the authoritative
    list is closed and its detail-agreeing application end date is today or
    earlier.  The rule deliberately does not depend on an ever-growing docNo
    allowlist, while a premature close, unknown action, duplicate control, or
    open row outside its declared application window remains a hard failure.
    """

    try:
        start = date.fromisoformat(_clean(apply_start))
        end = date.fromisoformat(_clean(apply_end))
    except ValueError as exc:
        raise BusanDongnaeContractError(
            "Dongnae application decision has invalid dates"
        ) from exc
    if end < start:
        raise BusanDongnaeContractError(
            "Dongnae application decision has reversed dates"
        )
    actions = tuple(_clean(action) for action in control_actions)
    if len(actions) > 1:
        raise BusanDongnaeContractError("multiple Dongnae application controls")
    action = actions[0] if actions else ""

    if source_status == "접수중":
        if action not in _LOCAL_LOGIN_ACTIONS:
            raise BusanDongnaeContractError(
                "open Dongnae course lacks identity-bound login control"
            )
        if not start <= cutoff <= end:
            raise BusanDongnaeContractError(
                "open Dongnae course is outside its verified application period"
            )
        return True, False, "active_login"

    if source_status == "마감":
        if not action:
            return False, False, "absent"
        if action == _LOCAL_CLOSED_ALERT_ACTION:
            return False, False, "closed_alert"
        if action in _LOCAL_LOGIN_ACTIONS:
            if end > cutoff:
                raise BusanDongnaeContractError(
                    "closed Dongnae login control precedes verified application end"
                )
            return False, True, "stale_login_suppressed"
        raise BusanDongnaeContractError(
            "closed Dongnae application control changed"
        )

    if source_status == "접수예정":
        if action:
            raise BusanDongnaeContractError(
                "scheduled Dongnae course unexpectedly exposes a control"
            )
        return False, False, "scheduled_no_control"

    raise BusanDongnaeContractError("unknown Dongnae detail source status")


def _exact_response_scope(final_url: str, host: str, path: str) -> Mapping[str, list[str]]:
    parsed = urlparse(final_url)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != host
        or parsed.port is not None
        or parsed.path != path
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise BusanDongnaeContractError("response escaped the audited URL scope")
    return parse_qs(parsed.query, keep_blank_values=True)


def _local_contract(
    soup: BeautifulSoup, final_url: str, *, page: int
) -> tuple[int, int, Optional[Tag]]:
    query = _exact_response_scope(final_url, BUSAN_DONGNAE_HOST, BUSAN_DONGNAE_PATH)
    expected_query = {"menuCd": [BUSAN_DONGNAE_MENU]}
    if page != 1:
        expected_query["pageno"] = [str(page)]
    if query != expected_query:
        raise BusanDongnaeContractError("Dongnae list response query changed")
    title = _text(_one(soup.select("title"), "Dongnae list title"))
    if title != "수강신청 < 평생학습강좌신청":
        raise BusanDongnaeContractError("Dongnae list title changed")
    form = _one(soup.select("form#frmSearch[name='frmSearch']"), "Dongnae search form")
    action = urlparse(_clean(form.get("action")))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != "/culture/index.dongnae"
        or parse_qs(action.query) != {"menuCd": [BUSAN_DONGNAE_MENU]}
    ):
        raise BusanDongnaeContractError("Dongnae list form changed")
    total_node = _one(soup.select("p.board_total"), "Dongnae total control")
    total_match = _LOCAL_TOTAL_RE.fullmatch(_text(total_node))
    if not total_match:
        raise BusanDongnaeContractError("Dongnae total/page control changed")
    total, displayed_page, last = (
        int(total_match.group(1).replace(",", "")),
        int(total_match.group(2)),
        int(total_match.group(3)),
    )
    if displayed_page != page or total < 1 or last != math.ceil(total / 25):
        raise BusanDongnaeContractError("Dongnae displayed pagination changed")
    tables = soup.select("table.basic.bbs_list")
    table = _one(tables, "Dongnae course table")
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _LOCAL_HEADERS:
        raise BusanDongnaeContractError("Dongnae list headers changed")
    rows = table.select("tbody tr")
    if page <= last:
        expected = 25 if page < last else total - 25 * (last - 1)
        if len(rows) != expected:
            raise BusanDongnaeContractError("Dongnae page row count changed")
        return total, last, table
    if page == last + 1:
        if rows:
            raise BusanDongnaeContractError("Dongnae sentinel is not empty")
        return total, last, None
    raise BusanDongnaeContractError("Dongnae request passed sentinel boundary")


def _parse_local_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last, table = _local_contract(soup, final_url, page=page)
    if expected_total is not None and total != expected_total:
        raise BusanDongnaeContractError("Dongnae source total changed during snapshot")
    if expected_last is not None and last != expected_last:
        raise BusanDongnaeContractError("Dongnae final page changed during snapshot")
    rows: list[dict[str, Any]] = []
    for position, source_row in enumerate(table.select("tbody tr") if table else [], 1):
        cells = source_row.find_all("td", recursive=False)
        if len(cells) != 7:
            raise BusanDongnaeContractError("Dongnae list row schema changed")
        link = _one(cells[1].select("a[href]"), "Dongnae course link")
        parsed = urlparse(urljoin(BUSAN_DONGNAE_URL, _clean(link.get("href"))))
        query_match = _LOCAL_DETAIL_QUERY_RE.fullmatch(parsed.query)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").lower() != BUSAN_DONGNAE_HOST
            or parsed.path != BUSAN_DONGNAE_PATH
            or parsed.params
            or parsed.fragment
            or parsed.username
            or parsed.password
            or query_match is None
        ):
            raise BusanDongnaeContractError("unsafe Dongnae detail identity")
        identity = query_match.group(1)
        onclick = _LOCAL_ONCLICK_RE.fullmatch(_clean(source_row.get("onclick")))
        onclick_parsed = urlparse(
            urljoin(BUSAN_DONGNAE_URL, onclick.group(1) if onclick else "")
        )
        onclick_query = _LOCAL_DETAIL_QUERY_RE.fullmatch(onclick_parsed.query)
        if (
            not onclick
            or onclick_parsed.scheme.lower() != "https"
            or (onclick_parsed.hostname or "").lower() != BUSAN_DONGNAE_HOST
            or onclick_parsed.path != BUSAN_DONGNAE_PATH
            or onclick_query is None
            or onclick_query.group(1) != identity
        ):
            raise BusanDongnaeContractError("Dongnae row/link identities differ")
        title = _text(link)
        # The official source leaves literal ampersands unescaped inside its
        # decorative ``title`` query parameter.  It is therefore not an
        # identity field; the visible link text and docNo are authoritative.
        if not title:
            raise BusanDongnaeContractError("Dongnae row title identity changed")
        source_status = _text(cells[0])
        if source_status not in _LOCAL_STATUS_MAP:
            raise BusanDongnaeContractError("unknown Dongnae source status")
        apply_start, apply_end = _date_pair(_text(cells[2]), "Dongnae application dates")
        start, end = _date_pair(_text(cells[3]), "Dongnae education dates")
        schedule = _text(cells[4]).removeprefix("교육시간 ")
        venue = _text(cells[5]).removeprefix("교육장소 ")
        capacity = _text(cells[6])
        if not schedule or not venue or not capacity:
            raise BusanDongnaeContractError("Dongnae safe list field is empty")
        rows.append(
            {
                "provider": BUSAN_DONGNAE_PROVIDER,
                "provider_course_id": f"{BUSAN_DONGNAE_PROVIDER}:education:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": "동래구 평생학습",
                "branch_code": "dongnae-lifelong",
                "preserve_branch": True,
                "category": "평생학습",
                "program_type": "교육/강좌",
                "raw_url": busan_dongnae_detail_url(identity),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _LOCAL_STATUS_MAP[source_status],
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": schedule,
                "fee": "",
                "capacity": capacity,
                "target": "",
                "venue_name": venue,
                "provider_organizer": "동래구청",
                "municipality_code": BUSAN_DONGNAE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_DONGNAE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "동래구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_DONGNAE_PARSER,
                    "source_catalog": "dongnae_complete_lifelong_catalogue",
                    "source_identity": identity,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "applicant_list_fetched": False,
                    "service_family": "education",
                },
            }
        )
    return rows, total, last


def _signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        repr(
            [
                (
                    _clean(row.get("provider_course_id")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _safe_table_values(
    table: Tag, labels: Sequence[str], safe_labels: frozenset[str]
) -> tuple[dict[str, str], set[str]]:
    seen: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for row in table.select("tr"):
        children = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            if (
                index + 1 >= len(children)
                or children[index].name != "th"
                or children[index + 1].name != "td"
            ):
                raise BusanDongnaeContractError("Dongnae detail table pairing changed")
            label = _text(children[index])
            if label in seen:
                raise BusanDongnaeContractError("duplicate Dongnae detail field")
            seen.append(label)
            if label in safe_labels:
                safe[label] = _text(children[index + 1])
            else:
                skipped.add(label)
            index += 2
    if tuple(seen) != tuple(labels):
        raise BusanDongnaeContractError("Dongnae detail field order changed")
    return safe, skipped


def _parse_local_detail(
    soup: BeautifulSoup,
    final_url: str,
    parent: Mapping[str, Any],
    *,
    cutoff: date,
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    query = _exact_response_scope(final_url, BUSAN_DONGNAE_HOST, BUSAN_DONGNAE_PATH)
    if query != {"menuCd": [BUSAN_DONGNAE_DETAIL_MENU], "docNo": [identity]}:
        raise BusanDongnaeContractError("Dongnae detail response identity changed")
    if _text(_one(soup.select("title"), "Dongnae detail title")) != (
        "수강신청 < 평생학습강좌신청 < 교육보기"
    ):
        raise BusanDongnaeContractError("Dongnae detail title changed")
    form = _one(soup.select("form#frmWrite[name='frmWrite']"), "Dongnae detail form")
    if (
        _clean(form.get("method")).casefold() != "post"
        or urlparse(_clean(form.get("action"))).path != BUSAN_DONGNAE_PATH
    ):
        raise BusanDongnaeContractError("Dongnae detail form changed")
    contents = _one(form.select(":scope > input[name='contentsSid']"), "contentsSid")
    if not _clean(contents.get("value")).isdigit():
        raise BusanDongnaeContractError("Dongnae detail form identity changed")
    table = _one(form.select("table.tb_t2"), "Dongnae detail table")
    safe, skipped = _safe_table_values(
        table, _LOCAL_DETAIL_LABELS, _LOCAL_DETAIL_SAFE_LABELS
    )
    required_safe = _LOCAL_DETAIL_SAFE_LABELS - {"강좌상세정보URL"}
    if any(not safe.get(label) for label in required_safe):
        raise BusanDongnaeContractError("Dongnae safe detail value is empty")
    if safe["강좌명"] != _clean(parent.get("title")):
        raise BusanDongnaeContractError("Dongnae list/detail title mismatch")
    start, end = _date_pair(safe["교육기간"], "Dongnae detail education period")
    apply_start, apply_end = _date_pair(
        safe["접수기간"], "Dongnae detail application period"
    )
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanDongnaeContractError("Dongnae list/detail dates mismatch")
    controls: list[Tag] = []
    for node in form.select("div.btnArea_right a, div.btnArea_right button"):
        label = _text(node) or _clean(
            " ".join(image.get("alt", "") for image in node.select("img"))
        )
        if label == "신청":
            controls.append(node)
    source_status = _clean(raw.get("source_status"))
    active, stale_suppressed, control_kind = _local_application_decision(
        source_status=source_status,
        apply_start=_clean(parent.get("apply_start")),
        apply_end=_clean(parent.get("apply_end")),
        cutoff=cutoff,
        control_actions=[_clean(control.get("onclick")) for control in controls],
    )
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
            "schedule_raw": safe["교육시간"],
            "venue_name": safe["교육장소"],
            "fee": safe["수강료"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": active,
        "detail_application_control_kind": control_kind,
        "closed_stale_login_control_suppressed": stale_suppressed,
        "application_control_target_never_persisted": True,
        "contact_value_never_read": "교육문의전화" in skipped,
        "instructor_value_never_read": "강사명" in skipped,
        "free_form_detail_never_read": "교육내용" in skipped,
        "attachments_never_read": "첨부파일" in skipped,
        "enrollment_values_never_read": {"교육신청자", "대기자"}.issubset(skipped),
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_DONGNAE_OFFICE
    )
    if office is None or office.name != BUSAN_LIFELONG_DONGNAE_OFFICE_NAME:
        raise BusanDongnaeContractError("lifelong Dongnae office changed")
    if (
        office.ownership != "duplicate_dedicated_dongnae_owner"
        or office.municipality_code
        or office.municipality_name
    ):
        raise BusanDongnaeContractError("lifelong Dongnae ownership changed")
    return office


def _parse_platform_page(
    soup: BeautifulSoup,
    _final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    office = _platform_office()
    form_errors = _lifelong._form_errors(soup, office, page)
    if form_errors:
        raise BusanDongnaeContractError("; ".join(form_errors))
    last, last_errors = _lifelong._advertised_last(soup)
    if last_errors:
        raise BusanDongnaeContractError("; ".join(last_errors))
    if expected_last is not None and last != expected_last:
        raise BusanDongnaeContractError("lifelong final page changed")
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if errors:
        raise BusanDongnaeContractError("; ".join(errors))
    if page <= last:
        if not rows:
            raise BusanDongnaeContractError("lifelong data page became empty")
    elif page == last + 1:
        if rows:
            raise BusanDongnaeContractError("lifelong sentinel is not empty")
    else:
        raise BusanDongnaeContractError("lifelong request passed sentinel")
    return rows, last


def _platform_semantic_multiset(rows: Sequence[Mapping[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )


def _platform_external_docno(row: Mapping[str, Any]) -> str:
    if _clean(row.get("raw_fields", {}).get("identity_kind")) != "external":
        raise BusanDongnaeContractError("lifelong row is not external")
    identity = canonical_busan_dongnae_course_identity(row.get("raw_url"))
    if not identity.startswith("docno:"):
        raise BusanDongnaeContractError(
            "lifelong external row left the canonical Dongnae detail scope"
        )
    return identity.removeprefix("docno:")


def _platform_training_test_matches(row: Mapping[str, Any]) -> bool:
    raw = row.get("raw_fields", {})
    expected = _AUDITED_PLATFORM_TRAINING_TEST
    return all(
        (
            _clean(raw.get("identity")) == expected["identity"],
            _clean(row.get("title")) == expected["title"],
            _clean(row.get("start_date")) == expected["start_date"],
            _clean(row.get("end_date")) == expected["end_date"],
            _clean(raw.get("source_status")) == expected["source_status"],
        )
    )


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(identity):
        raise BusanDongnaeContractError("invalid native lifelong identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_DONGNAE_PROVIDER,
            "provider_course_id": f"{BUSAN_DONGNAE_PROVIDER}:lifelong:{identity}",
            "prefer_incoming_provider_course_id": True,
            "branch": BUSAN_LIFELONG_DONGNAE_OFFICE_NAME,
            "branch_code": "dongnae-lifelong-office00002682",
            "preserve_branch": True,
            "provider_organizer": BUSAN_LIFELONG_DONGNAE_OFFICE_NAME,
            "municipality_code": BUSAN_DONGNAE_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_DONGNAE_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "동래구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "complete_shared_office_census+native_current_detail",
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_DONGNAE_PARSER,
        "source_catalog": "busan_lifelong_dongnae_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_detail_values(soup: BeautifulSoup) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in soup.select("div.form_group dl"):
        heading = _one(definition.find_all("dt", recursive=False), "platform detail label")
        value = _one(definition.find_all("dd", recursive=False), "platform detail value")
        label = _text(heading)
        if label in labels:
            raise BusanDongnaeContractError("duplicate platform detail field")
        labels.append(label)
        if label in _PLATFORM_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = tuple(label for label in labels if label not in _PLATFORM_OPTIONAL_LABELS)
    if required != _PLATFORM_DETAIL_REQUIRED:
        raise BusanDongnaeContractError("platform detail field order changed")
    return labels, safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    query = _exact_response_scope(
        final_url, _lifelong.BUSAN_LIFELONG_HOST, BUSAN_LIFELONG_DETAIL_PATH
    )
    if query != {"lng_id": [identity]}:
        raise BusanDongnaeContractError("platform detail response identity changed")
    for name, expected in (
        ("lng_id", identity),
        ("inst_id", BUSAN_LIFELONG_DONGNAE_OFFICE),
    ):
        fields = {_clean(node.get("value")) for node in soup.select(f"input[name='{name}']")}
        if fields != {expected}:
            raise BusanDongnaeContractError(f"platform detail {name} changed")
    heading = _one(soup.select("h2.enrolTit"), "platform detail title")
    prefix = _text(_one(heading.select(":scope > span"), "platform office prefix"))
    if prefix != f"[{BUSAN_LIFELONG_DONGNAE_OFFICE_NAME}]":
        raise BusanDongnaeContractError("platform detail office changed")
    direct_title = _clean(
        " ".join(
            str(child) for child in heading.children if isinstance(child, NavigableString)
        )
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanDongnaeContractError("platform list/detail title mismatch")
    _labels, safe, skipped = _platform_detail_values(soup)
    if any(not safe.get(label) for label in _PLATFORM_SAFE_LABELS if label in _labels):
        raise BusanDongnaeContractError("platform safe detail value is empty")
    start, end = _date_pair(safe["교육기간"], "platform education period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanDongnaeContractError("platform list/detail dates mismatch")
    controls = soup.select("#learning_aply_btn")
    if len(controls) > 1:
        raise BusanDongnaeContractError("multiple platform application controls")
    control_label = _text(controls[0]) if controls else ""
    source_apply_status = safe["신청상태"]
    active = bool(
        len(controls) == 1
        and "접수중" in source_apply_status
        and _clean(controls[0].get("onclick")) == "fn_learning_apply(); return false;"
        and control_label in {"일반모집신청", "대기자신청", "우선모집신청"}
    )
    if controls and not active:
        raise BusanDongnaeContractError("platform application control/status changed")
    result = dict(parent)
    if active:
        status = "OPEN"
        application_type = (
            "WAITLIST_APPLY" if control_label == "대기자신청" else "ONLINE_RESERVATION"
        )
    else:
        status = "SCHEDULED" if "접수대기" in source_apply_status else "CLOSED"
        application_type = "INFO_ONLY"
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "status": status,
            "target": safe["교육대상"],
            "venue_name": safe["교육장소"],
            "fee": safe["수강료"],
            "schedule_raw": safe["교육시간"],
            "application_method_raw": safe["모집방법"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": active,
        "detail_application_control_label": control_label,
        "detail_source_status": source_apply_status,
        "contact_value_never_read": "문의전화" in skipped,
        "enrollment_value_never_read": "접수인원" in skipped,
        "instructor_value_never_read": "강사" in skipped,
        "attachments_never_read": {"강좌소개 첨부파일", "강의계획서"}.issubset(skipped),
        "free_form_values_never_read": {"강좌소개", "주의사항", "검색키워드", "강좌제한"}.issubset(skipped),
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, final_url: str, *, page: int
) -> tuple[int, Optional[Tag]]:
    query = _exact_response_scope(final_url, BUSAN_CITY_HOST, BUSAN_CITY_LIST_PATH)
    if query != {
        "curPage": [str(page)],
        "srchGugun": [BUSAN_CITY_DONGNAE_GUGUN],
        "srchResveInsttCd": [BUSAN_CITY_RESIDENT_OFFICE],
    }:
        raise BusanDongnaeContractError("Busan city list response query changed")
    if _text(_one(soup.select("title"), "Busan city list title")) != _CITY_LIST_TITLE:
        raise BusanDongnaeContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanDongnaeContractError("Busan city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "city curPage")
    if _clean(page_field.get("value")) != str(page):
        raise BusanDongnaeContractError("Busan city form page changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_DONGNAE_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanDongnaeContractError(f"Busan city {name} filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "city final page")
    parsed_end = urlparse(urljoin(BUSAN_CITY_DONGNAE_URL, _clean(end_link.get("href"))))
    end_query = parse_qs(parsed_end.query, keep_blank_values=True)
    if (
        (parsed_end.hostname or "").lower() != BUSAN_CITY_HOST
        or parsed_end.path != BUSAN_CITY_LIST_PATH
        or set(end_query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or end_query.get("srchGugun") != [BUSAN_CITY_DONGNAE_GUGUN]
        or end_query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
        or len(end_query.get("curPage", [])) != 1
        or not end_query["curPage"][0].isdigit()
    ):
        raise BusanDongnaeContractError("unsafe Busan city final-page control")
    last = int(end_query["curPage"][0])
    roots = soup.select("ul.reserveList")
    if page <= last:
        return last, _one(roots, "Busan city reserve list")
    if page == last + 1:
        if roots:
            raise BusanDongnaeContractError("Busan city sentinel is not empty")
        return last, None
    raise BusanDongnaeContractError("Busan city request passed sentinel")


def _city_date_ranges(value: Any) -> tuple[str, str, str, str]:
    match = _CITY_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanDongnaeContractError("Busan city card dates changed")
    parts = [date.fromisoformat(value).isoformat() for value in match.groups()]
    if parts[1] < parts[0] or parts[3] < parts[2]:
        raise BusanDongnaeContractError("Busan city card date range is reversed")
    return parts[0], parts[1], parts[2], parts[3]


def _city_application_method(identity: str, value: Any) -> str:
    source = _clean(value)
    normalized = ", ".join(
        part for part in (_clean(part) for part in source.split(",")) if part
    )
    if source != normalized and _AUDITED_CITY_METHOD_ARTIFACTS.get(identity) != (
        source,
        normalized,
    ):
        raise BusanDongnaeContractError("Busan city method separator artifact changed")
    return normalized


def _parse_city_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(soup, final_url, page=page)
    if expected_last is not None and last != expected_last:
        raise BusanDongnaeContractError("Busan city final page changed")
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(root.find_all("li", recursive=False) if root else [], 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "city course link")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanDongnaeContractError("Busan city identity action changed")
        group_id, program_id = action.groups()
        identity = f"{group_id}:{program_id}"
        title_node = _one(link.select(":scope .tit"), "city course title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        if not title or (
            title_attribute != title
            and _AUDITED_CITY_TITLE_ATTRIBUTE.get(identity)
            != (title, title_attribute)
        ):
            raise BusanDongnaeContractError("Busan city card title changed")
        source_status = _text(_one(link.select(":scope .statusMark"), "city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanDongnaeContractError("unknown Busan city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanDongnaeContractError("Busan city card labels changed")
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanDongnaeContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if not branch.startswith("동래구 ") or not branch.endswith(" 주민자치회"):
            raise BusanDongnaeContractError("Busan city course left Dongnae owner")
        apply_start, apply_end, start, end = _city_date_ranges(safe["일자"])
        source_method = safe["방법"]
        method = _city_application_method(identity, source_method)
        rows.append(
            {
                "provider": BUSAN_DONGNAE_PROVIDER,
                "provider_course_id": f"{BUSAN_DONGNAE_PROVIDER}:reserve:{group_id}:{program_id}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"dongnae-reserve-{group_id}",
                "preserve_branch": True,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": busan_dongnae_city_detail_url(group_id, program_id),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": method,
                "reservation_available": False,
                "status": _CITY_STATUS_MAP[source_status],
                "fee": "",
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": "",
                "target": safe["대상"],
                "venue_name": safe["장소"],
                "provider_organizer": branch,
                "municipality_code": BUSAN_DONGNAE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_DONGNAE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "동래구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_DONGNAE_PARSER,
                    "source_catalog": "busan_reserve_dongnae_resident_councils",
                    "source_identity": identity,
                    "audited_title_attribute_anomaly": (
                        identity in _AUDITED_CITY_TITLE_ATTRIBUTE
                    ),
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": method,
                    "source_application_method_display": source_method,
                    "audited_method_separator_artifact": (
                        identity in _AUDITED_CITY_METHOD_ARTIFACTS
                    ),
                    "source_card_dates": safe["일자"],
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "applicant_list_fetched": False,
                    "service_family": "education",
                },
            }
        )
    return rows, last


def _city_detail_values(info: Tag) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(definition.find_all("dt", recursive=False), "city detail label")
        value = _one(definition.find_all("dd", recursive=False), "city detail value")
        label = _text(heading)
        if label in labels:
            raise BusanDongnaeContractError("duplicate city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        else:
            raise BusanDongnaeContractError(f"unknown city detail field {label!r}")
    without_attachment = tuple(label for label in labels if label != "첨부파일")
    if without_attachment != _CITY_DETAIL_REQUIRED or "문의전화" not in skipped:
        raise BusanDongnaeContractError("city detail field order changed")
    return labels, safe, skipped


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    query = _exact_response_scope(final_url, BUSAN_CITY_HOST, BUSAN_CITY_DETAIL_PATH)
    if query != {"resveGroupSn": [group_id], "progrmSn": [program_id]}:
        raise BusanDongnaeContractError("Busan city detail response identity changed")
    if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
        raise BusanDongnaeContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanDongnaeContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanDongnaeContractError("Busan city detail identity changed")
    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city title")
    source_status = _text(_one(heading.select(":scope .statusMark"), "city status"))
    direct_title = _clean(
        " ".join(
            str(child) for child in heading.children if isinstance(child, NavigableString)
        )
    )
    if direct_title != _clean(parent.get("title")) or source_status != _clean(
        raw.get("source_status")
    ):
        raise BusanDongnaeContractError("Busan city list/detail heading mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "city safe detail values",
    )
    _labels, safe, skipped = _city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanDongnaeContractError("city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanDongnaeContractError("city free-form boundary changed")
    start, end = _date_pair(safe["운영기간"], "city operating period")
    apply_start, apply_end = _date_pair(safe["신청기간"], "city application period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanDongnaeContractError("city list/detail dates mismatch")
    if safe["신청방법"] != _clean(raw.get("source_application_method")) or safe[
        "운영기관"
    ] != _clean(parent.get("branch")) or safe["대상"] != _clean(parent.get("target")):
        raise BusanDongnaeContractError("city list/detail safe value mismatch")
    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanDongnaeContractError("multiple city application controls")
    control_label = _text(controls[0]) if controls else ""
    status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    application_type = "INFO_ONLY"
    if status == "OPEN" and "온라인" in method:
        if len(controls) != 1 or not any(token in control_label for token in ("신청", "예약")):
            raise BusanDongnaeContractError("open online city course lacks control")
        active = True
        application_type = "ONLINE_RESERVATION"
    elif status == "OPEN" and any(token in method for token in ("방문", "전화")):
        if control_label not in {"", "방문예약"}:
            raise BusanDongnaeContractError("offline city application control changed")
        application_type = "OFFLINE_APPLY"
    elif status == "CLOSED" and control_label not in {"", "접수마감"}:
        raise BusanDongnaeContractError("closed city control changed")
    elif status == "SCHEDULED" and control_label not in {"", "대기중", "접수대기"}:
        raise BusanDongnaeContractError("scheduled city control changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": control_label,
        "inquiry_value_never_read": True,
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    return any(
        token in lowered
        for token in (
            "phone",
            "telephone",
            "email",
            "instructor",
            "teacher",
            "강사",
            "전화",
            "메일",
            "applicant",
        )
    )


def _scrub_text(value: Any) -> tuple[str, int]:
    text = _clean(value)
    text, phones = _PHONE_RE.subn("[redacted]", text)
    text, emails = _EMAIL_RE.subn("[redacted]", text)
    return text, phones + emails


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def visit(value: Any) -> Any:
        nonlocal redactions
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if _pii_key(key):
                    redactions += 1
                    continue
                result[str(key)] = visit(item)
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if isinstance(value, str):
            cleaned, count = _scrub_text(value)
            redactions += count
            return cleaned
        return value

    return visit(row), redactions


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "provider": BUSAN_DONGNAE_PROVIDER,
        "municipality_code": BUSAN_DONGNAE_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_DONGNAE_MUNICIPALITY_NAME,
        "canonical_url": BUSAN_DONGNAE_URL,
        "city_canonical_url": BUSAN_CITY_DONGNAE_URL,
        "lifelong_office_code": BUSAN_LIFELONG_DONGNAE_OFFICE,
        "ownership_scope": BUSAN_DONGNAE_OWNERSHIP_SCOPE,
        "candidate_ids": dict(BUSAN_DONGNAE_CANDIDATE_IDS),
        "owner_boundary_audit": dict(BUSAN_DONGNAE_OWNER_BOUNDARY_AUDIT),
        "pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "local_closed_stale_login_control_count": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_busan_dongnae_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 120,
    detail_limit: int = 180,
    max_requests: int = 260,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_DONGNAE_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future snapshot of all three ledgers."""

    meta = _base_meta()
    if not is_busan_dongnae_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Busan Dongnae education owner"
        )
        return [], BUSAN_DONGNAE_PARSER, meta
    try:
        if any(
            isinstance(value, bool)
            for value in (timeout, max_pages, detail_limit, max_requests, max_workers)
        ):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_DONGNAE_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_DONGNAE_PARSER, meta
    if page_cap < 1 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps cannot inspect all three ledgers"
        return [], BUSAN_DONGNAE_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def account(result: _FetchResult, *, list_phase: bool) -> Any:
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += 1
            meta["pages"] += 1
        return result.value

    def fetch_one(url: str, parser: Callable[[BeautifulSoup, str], Any], *, list_phase: bool):
        result = _fetch_parsed(
            url,
            parser,
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            sleeper=sleeper,
            budget=budget,
        )
        return account(result, list_phase=list_phase)

    def fetch_batch(
        items: Sequence[tuple[Any, str, Callable[[BeautifulSoup, str], Any]]],
        *,
        list_phase: bool,
    ) -> dict[Any, Any]:
        values, retries, sessions = _fetch_many(
            items,
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            sleeper=sleeper,
            budget=budget,
            max_workers=workers,
        )
        meta["network_retry_count"] += retries
        meta["sessions_created"] += sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += len(items)
            meta["pages"] += len(items)
        return values

    try:
        # Dedicated Dongnae catalogue.
        first_rows, local_total, local_last = fetch_one(
            busan_dongnae_list_url(1),
            lambda soup, final: _parse_local_page(soup, final, page=1),
            list_phase=True,
        )
        if local_last > page_cap:
            raise BusanDongnaeContractError(
                f"max_pages cap allows {page_cap} of {local_last} Dongnae pages"
            )
        local_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
        if local_last > 1:
            items = [
                (
                    page,
                    busan_dongnae_list_url(page),
                    lambda soup, final, p=page: _parse_local_page(
                        soup,
                        final,
                        page=p,
                        expected_total=local_total,
                        expected_last=local_last,
                    )[0],
                )
                for page in range(2, local_last + 1)
            ]
            local_pages.update(fetch_batch(items, list_phase=True))
        sentinel_rows, _, _ = fetch_one(
            busan_dongnae_list_url(local_last + 1),
            lambda soup, final: _parse_local_page(
                soup,
                final,
                page=local_last + 1,
                expected_total=local_total,
                expected_last=local_last,
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if sentinel_rows:
            raise BusanDongnaeContractError("Dongnae sentinel returned rows")
        recheck_pages = sorted({1, local_last})
        rechecked = fetch_batch(
            [
                (
                    page,
                    busan_dongnae_list_url(page),
                    lambda soup, final, p=page: _parse_local_page(
                        soup,
                        final,
                        page=p,
                        expected_total=local_total,
                        expected_last=local_last,
                    )[0],
                )
                for page in recheck_pages
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(recheck_pages)
        for page in recheck_pages:
            if _signature(rechecked[page]) != _signature(local_pages[page]):
                raise BusanDongnaeContractError("Dongnae boundary page changed")
        local_rows = [row for page in range(1, local_last + 1) for row in local_pages[page]]
        if len(local_rows) != local_total:
            raise BusanDongnaeContractError("Dongnae complete source count changed")
        local_ids = [_clean(row["raw_fields"]["source_identity"]) for row in local_rows]
        if len(local_ids) != len(set(local_ids)):
            raise BusanDongnaeContractError("duplicate Dongnae docNo")

        # Platform: require two identical complete censuses and both sentinels.
        platform_censuses: list[list[dict[str, Any]]] = []
        platform_last = 0
        for census_index in range(2):
            page_rows, current_last = fetch_one(
                busan_dongnae_lifelong_list_url(1),
                lambda soup, final: _parse_platform_page(soup, final, page=1),
                list_phase=True,
            )
            if current_last > page_cap:
                raise BusanDongnaeContractError(
                    f"max_pages cap allows {page_cap} of {current_last} platform pages"
                )
            if current_last != 1:
                raise BusanDongnaeContractError(
                    "pageUnit1000 no longer contains the complete platform census"
                )
            empty, sentinel_last = fetch_one(
                busan_dongnae_lifelong_list_url(2),
                lambda soup, final: _parse_platform_page(
                    soup, final, page=2, expected_last=current_last
                ),
                list_phase=True,
            )
            meta["sentinel_requests"] += 1
            if empty or sentinel_last != current_last:
                raise BusanDongnaeContractError("platform sentinel changed")
            if census_index:
                meta["stability_rechecks"] += 2
            platform_last = current_last
            platform_censuses.append(page_rows)
        if _platform_semantic_multiset(platform_censuses[0]) != _platform_semantic_multiset(
            platform_censuses[1]
        ):
            raise BusanDongnaeContractError("platform complete censuses changed")
        platform_rows = platform_censuses[0]
        sequences = sorted(int(row["raw_fields"]["list_sequence"]) for row in platform_rows)
        if sequences != list(range(1, len(platform_rows) + 1)):
            raise BusanDongnaeContractError("platform complete source sequence changed")
        external_rows = [
            row
            for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "external"
        ]
        native_source_rows = [
            row
            for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "internal"
        ]
        if len(external_rows) + len(native_source_rows) != len(platform_rows):
            raise BusanDongnaeContractError("unexpected platform identity family")
        external_docnos = [_platform_external_docno(row) for row in external_rows]
        if len(external_docnos) != len(set(external_docnos)):
            raise BusanDongnaeContractError("repeated platform external docNo")
        unmatched = sorted(set(external_docnos) - set(local_ids))
        if unmatched:
            raise BusanDongnaeContractError(
                "platform external docNo absent from canonical census: " + unmatched[0]
            )
        training_rows = [row for row in native_source_rows if _platform_training_test_matches(row)]
        if len(training_rows) != 1:
            raise BusanDongnaeContractError("audited platform training test changed")
        native_rows = [
            _platform_native_row(row)
            for row in native_source_rows
            if row is not training_rows[0]
        ]

        # Exact Busan integrated-reservation resident-council partition.
        city_first, city_last = fetch_one(
            busan_dongnae_city_list_url(1),
            lambda soup, final: _parse_city_page(soup, final, page=1),
            list_phase=True,
        )
        if city_last > page_cap:
            raise BusanDongnaeContractError(
                f"max_pages cap allows {page_cap} of {city_last} city pages"
            )
        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first}
        if city_last > 1:
            city_pages.update(
                fetch_batch(
                    [
                        (
                            page,
                            busan_dongnae_city_list_url(page),
                            lambda soup, final, p=page: _parse_city_page(
                                soup, final, page=p, expected_last=city_last
                            )[0],
                        )
                        for page in range(2, city_last + 1)
                    ],
                    list_phase=True,
                )
            )
        city_empty, _ = fetch_one(
            busan_dongnae_city_list_url(city_last + 1),
            lambda soup, final: _parse_city_page(
                soup, final, page=city_last + 1, expected_last=city_last
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if city_empty:
            raise BusanDongnaeContractError("Busan city sentinel returned rows")
        city_recheck_pages = sorted({1, city_last})
        city_rechecked = fetch_batch(
            [
                (
                    page,
                    busan_dongnae_city_list_url(page),
                    lambda soup, final, p=page: _parse_city_page(
                        soup, final, page=p, expected_last=city_last
                    )[0],
                )
                for page in city_recheck_pages
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(city_recheck_pages)
        for page in city_recheck_pages:
            if _signature(city_rechecked[page]) != _signature(city_pages[page]):
                raise BusanDongnaeContractError("Busan city boundary page changed")
        city_rows = [row for page in range(1, city_last + 1) for row in city_pages[page]]
        city_ids = [_clean(row.get("provider_course_id")) for row in city_rows]
        if len(city_ids) != len(set(city_ids)):
            raise BusanDongnaeContractError("duplicate Busan city identity")

        local_current = [
            row for row in local_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        native_current = [
            row for row in native_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        city_current = [
            row for row in city_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        current = local_current + native_current + city_current
        if len(current) > detail_cap:
            raise BusanDongnaeContractError(
                f"detail_limit cap allows {detail_cap} of {len(current)} current details"
            )

        detail_items: list[tuple[str, str, Callable[[BeautifulSoup, str], dict[str, Any]]]] = []
        for row in local_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]),
                    _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_local_detail(
                        soup, final, parent, cutoff=cutoff
                    ),
                )
            )
        for row in native_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]),
                    _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_platform_detail(
                        soup, final, parent
                    ),
                )
            )
        for row in city_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]),
                    _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_city_detail(soup, final, parent),
                )
            )
        enriched_by_id = fetch_batch(detail_items, list_phase=False) if detail_items else {}
        meta["detail_pages"] = len(detail_items)
        enriched = [enriched_by_id[_clean(row["provider_course_id"])] for row in current]

        sanitized: list[dict[str, Any]] = []
        redactions = 0
        for row in enriched:
            safe_row, count = _sanitize_row(row)
            sanitized.append(safe_row)
            redactions += count
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(sanitized))
        before_ids = [_clean(row.get("provider_course_id")) for row in sanitized]
        after_ids = [_clean(row.get("provider_course_id")) for row in result]
        if len(result) != len(sanitized) or Counter(after_ids) != Counter(before_ids):
            raise BusanDongnaeContractError("dedupe changed the complete identity set")
        if len(after_ids) != len(set(after_ids)):
            raise BusanDongnaeContractError("duplicate identity remained after dedupe")

        meta.update(
            {
                "local_source_rows": len(local_rows),
                "local_data_pages": local_last,
                "local_current_count": len(local_current),
                "local_expired_count": len(local_rows) - len(local_current),
                "platform_source_rows": len(platform_rows),
                "platform_data_pages": platform_last,
                "platform_external_duplicate_rows": len(external_rows),
                "platform_external_unique_docnos": len(set(external_docnos)),
                "platform_external_repeated_rows": len(external_docnos)
                - len(set(external_docnos)),
                "platform_external_matching_canonical": len(external_docnos),
                "platform_native_rows": len(native_source_rows),
                "platform_training_test_rows": len(training_rows),
                "platform_native_publishable_rows": len(native_rows),
                "platform_native_current_count": len(native_current),
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "city_expired_count": len(city_rows) - len(city_current),
                "source_total": len(local_rows) + len(platform_rows) + len(city_rows),
                "duplicate_source_rows": len(external_rows),
                "unique_education_source_rows": len(local_rows)
                + len(native_rows)
                + len(city_rows),
                "current_source_count": len(current),
                "expired_count": len(local_rows)
                - len(local_current)
                + len(native_rows)
                - len(native_current)
                + len(city_rows)
                - len(city_current),
                "status_counts": dict(Counter(row.get("status") for row in result)),
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "local_closed_stale_login_control_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "closed_stale_login_control_suppressed"
                        )
                    )
                    for row in result
                ),
                "branch_counts": dict(Counter(row.get("branch") for row in result)),
                "pii_redaction_count": redactions,
                "required_list_requests": meta["list_requests"],
                "required_detail_requests": len(detail_items),
                "network_requests": budget.count,
                "pagination_detected": any(last > 1 for last in (local_last, city_last)),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "all unique education rows ended before the crawl date"
                    if not result
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
        return result, BUSAN_DONGNAE_PARSER, meta
    except Exception as exc:
        meta["network_requests"] = budget.count
        message = _clean(exc)
        if "cap" in message:
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = message or exc.__class__.__name__
        return [], BUSAN_DONGNAE_PARSER, meta


collect_courses = collect_busan_dongnae_education


__all__ = [
    "BUSAN_DONGNAE_PROVIDER",
    "BUSAN_DONGNAE_CANDIDATE_ID",
    "BUSAN_DONGNAE_ALIAS_PROVIDER",
    "BUSAN_CITY_DONGNAE_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_DONGNAE_MUNICIPALITY_CODE",
    "BUSAN_DONGNAE_MUNICIPALITY_NAME",
    "BUSAN_DONGNAE_URL",
    "BUSAN_DONGNAE_CANONICAL_URL",
    "BUSAN_DONGNAE_ALIAS_URL",
    "BUSAN_CITY_DONGNAE_URL",
    "BUSAN_LIFELONG_DONGNAE_OFFICE",
    "BUSAN_DONGNAE_PARSER",
    "BUSAN_DONGNAE_CANDIDATE_IDS",
    "BUSAN_DONGNAE_OWNER_BOUNDARY_AUDIT",
    "BUSAN_DONGNAE_DISCOVERY_AUDIT",
    "BusanDongnaeContractError",
    "is_busan_dongnae_education_target",
    "is_target",
    "busan_dongnae_list_url",
    "busan_dongnae_detail_url",
    "busan_dongnae_city_list_url",
    "busan_dongnae_city_detail_url",
    "busan_dongnae_lifelong_list_url",
    "busan_dongnae_lifelong_detail_url",
    "canonical_busan_dongnae_course_identity",
    "collect_busan_dongnae_education",
    "collect_courses",
]
