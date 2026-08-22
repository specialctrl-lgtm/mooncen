"""Atomic education collector for Busan Haeundae-gu's public ledgers.

The search candidate registered for Haeundae is the static ``사이버교육``
information page.  The real district owner is the unfiltered ``강좌·교육``
catalogue on Haeundae's integrated-reservation site.  This module retains the
registered provider/candidate identity while always collecting that canonical
catalogue.

Two companion sources belong to the same municipal snapshot.  부산평생학습
office ``OFFICE_00002635`` republishes district ``res_no`` records; exact
identity and education-period checks suppress those copies while preserving
future native ``LEARNING_*`` records.  부산광역시 통합예약 is restricted to
the exact Haeundae resident-council partition (``srchGugun=16`` and
``srchResveInsttCd=33``).

Every advertised page, its immediate empty sentinel, stable boundary pages,
two complete platform censuses, and every current/future safe detail are
mandatory.  Any source drift discards the complete union.  Applicant/account
pages, application forms, contact/instructor values, enrolment values,
attachments, and free-form descriptions are never fetched or persisted.
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


BUSAN_HAEUNDAE_PROVIDER = "MUNI_WWW_HAEUNDAE_GO_KR_E2AD27FA"
BUSAN_HAEUNDAE_CANDIDATE_ID = "MUNI_IR_773030FA7D76"
BUSAN_CITY_HAEUNDAE_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_506834D2"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_HAEUNDAE_MUNICIPALITY_CODE = "2635000000"
BUSAN_HAEUNDAE_MUNICIPALITY_NAME = "부산광역시 해운대구"
BUSAN_HAEUNDAE_HOST = "www.haeundae.go.kr"
BUSAN_HAEUNDAE_PATH = "/reserve/index.do"
BUSAN_HAEUNDAE_REGISTERED_PATH = "/index.do"
BUSAN_HAEUNDAE_REGISTERED_MENU = "DOM_000001108002000000"
BUSAN_HAEUNDAE_LIST_MENU = "DOM_000000501005000000"
BUSAN_HAEUNDAE_DETAIL_MENU = "DOM_000000501006000000"
BUSAN_HAEUNDAE_APPLY_MENU = "DOM_000000501007000000"
BUSAN_HAEUNDAE_MY_RESERVATION_MENU = "DOM_000000507000000000"
BUSAN_HAEUNDAE_PLATFORM_DETAIL_MENU = "DOM_000000501009001000"
BUSAN_HAEUNDAE_REGISTERED_URL = (
    f"https://{BUSAN_HAEUNDAE_HOST}{BUSAN_HAEUNDAE_REGISTERED_PATH}?"
    + urlencode({"menuCd": BUSAN_HAEUNDAE_REGISTERED_MENU})
)
BUSAN_HAEUNDAE_CANONICAL_URL = (
    f"https://{BUSAN_HAEUNDAE_HOST}{BUSAN_HAEUNDAE_PATH}?"
    + urlencode({"menuCd": BUSAN_HAEUNDAE_LIST_MENU})
)
BUSAN_HAEUNDAE_URL = BUSAN_HAEUNDAE_CANONICAL_URL

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_HAEUNDAE_GUGUN = "16"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_HAEUNDAE_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_HAEUNDAE_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_LIFELONG_HAEUNDAE_OFFICE = "OFFICE_00002635"
BUSAN_LIFELONG_HAEUNDAE_OFFICE_NAME = "해운대구청"
BUSAN_LIFELONG_PAGE_SIZE = 1000
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_OFFICE_URL = _lifelong.BUSAN_LIFELONG_URL

BUSAN_HAEUNDAE_FETCH_ATTEMPTS = 3
BUSAN_HAEUNDAE_MAX_WORKERS = 12
BUSAN_HAEUNDAE_MAX_HTML_BYTES = 12_000_000
BUSAN_HAEUNDAE_PARSER = (
    "haeundae_complete_education_resno_pages+empty_sentinel+stable_first_last+"
    "lifelong_office00002635_pageunit1000_two_complete_censuses+"
    "external_resno_education_period_duplicate_proof+native_learning_details+"
    "busan_reserve_gugun16_office33_all_pages+empty_sentinel+stable_first_last+"
    "all_current_safe_details+pii_never_read+atomic_three_ledger_snapshot"
)
BUSAN_HAEUNDAE_OWNERSHIP_SCOPE = (
    "haeundae_complete_district_education_native_platform_courses_and_exact_"
    "busan_city_haeundae_resident_council_education"
)

BUSAN_HAEUNDAE_CANDIDATE_IDS: Mapping[str, str] = {
    "registered_static_page_retargeted_to_complete_owner": (
        BUSAN_HAEUNDAE_CANDIDATE_ID
    ),
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "busan_resident_councils": "MUNI_IR_6E08DDCBB806",
    "wrong_municipality_museum_detail": "MUNI_IR_2BA97ED12CEB",
    "wrong_municipality_family_detail": "MUNI_IR_5608F8475923",
}

BUSAN_HAEUNDAE_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_HAEUNDAE_PROVIDER: {
        "decision": "retain_provider_and_retarget_static_page_to_complete_owner",
        "candidate_id": BUSAN_HAEUNDAE_CANDIDATE_ID,
        "registered_url": BUSAN_HAEUNDAE_REGISTERED_URL,
        "canonical_url": BUSAN_HAEUNDAE_CANONICAL_URL,
        "identity_rule": "exact numeric res_no",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_external_resno_duplicates_keep_native_learning_ids",
        "candidate_id": BUSAN_HAEUNDAE_CANDIDATE_IDS[
            "busan_lifelong_federation"
        ],
        "office_code": BUSAN_LIFELONG_HAEUNDAE_OFFICE,
        "identity_rule": "district res_no plus exact education period",
    },
    BUSAN_CITY_HAEUNDAE_PROVIDER: {
        "decision": "collect_exact_haeundae_resident_council_partition",
        "candidate_id": BUSAN_HAEUNDAE_CANDIDATE_IDS[
            "busan_resident_councils"
        ],
        "url": BUSAN_CITY_HAEUNDAE_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_HAEUNDAE_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "WRONG_MUNICIPALITY_SEARCH_DETAILS": {
        "decision": "exclude",
        "candidate_ids": (
            BUSAN_HAEUNDAE_CANDIDATE_IDS["wrong_municipality_museum_detail"],
            BUSAN_HAEUNDAE_CANDIDATE_IDS["wrong_municipality_family_detail"],
        ),
    },
    "PRIVATE_BOUNDARY": {
        "decision": "never_fetch_or_persist",
        "reason": (
            "application forms, my-reservation/account pages, applicant values, "
            "contacts, instructors, attachments and free-form descriptions contain PII"
        ),
    },
}

# Exact non-course records present inside the district's education category.
# An identity may disappear when the archive is compacted.  If it remains, its
# title must still match before it is excluded.
_AUDITED_LOCAL_EXCLUSIONS: Mapping[str, tuple[str, str]] = {
    "2026060047": ("테스트(접수신청 연습용)", "training_test"),
    "2026060006": ("테스트(접수신청 연습용)", "training_test"),
    "2026050074": ("test입니다", "training_test"),
    "2025090133": ("장산역 스마트도서관 희망도서 신청", "library_request_not_course"),
    "2023090134": ("센텀시티역 스마트도서관 희망도서 신청", "library_request_not_course"),
    "2023020049": ("문화복합센터 스마트도서관 희망도서 신청", "library_request_not_course"),
    "2026050007": (
        "[공지] ‘협약병원 건강강좌’(행복한 부모가 될 준비 & 아가의 발달핑) 폐강 안내",
        "closure_notice_not_course",
    ),
}

_AUDITED_PLATFORM_TOMBSTONES: Mapping[str, tuple[str, str, str, str, str]] = {
    "2025070080": (
        "[유료과정] [해운대 청춘대학] 초경량비행장치(드론)조종자",
        "2025-08-10",
        "2025-09-21",
        "2025-07-31",
        "2025-08-04",
    ),
    "2025070027": (
        "[폐강]방송댄스",
        "2025-08-07",
        "2025-08-26",
        "2025-07-17",
        "2025-07-31",
    ),
}

# The district extended six July 2026 deadlines after the federation copied
# them.  Identity, title and education period remain exact.
_AUDITED_PLATFORM_APPLY_END_DRIFT: Mapping[str, tuple[str, str]] = {
    "2026070001": ("2026-07-20", "2026-07-24"),
    "2026070002": ("2026-07-20", "2026-07-24"),
    "2026070006": ("2026-07-20", "2026-07-27"),
    "2026070007": ("2026-07-20", "2026-07-22"),
    "2026070009": ("2026-07-20", "2026-07-26"),
    "2026070010": ("2026-07-20", "2026-07-24"),
}

_AUDITED_CITY_TITLE_ATTRIBUTE: Mapping[str, tuple[str, str]] = {
    "365:2362": ("두드림풍물패", "두드림풍물패 "),
}

BUSAN_HAEUNDAE_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "registered_url": BUSAN_HAEUNDAE_REGISTERED_URL,
    "registered_response": "HTTP 200 static cyber-education information page",
    "canonical_url": BUSAN_HAEUNDAE_CANONICAL_URL,
    "district_rows": 728,
    "district_data_pages": 46,
    "district_page_counts": {"1-45": 16, "46": 8},
    "district_sentinel_page": 47,
    "district_source_status_counts": {
        "접수마감": 694,
        "접수": 21,
        "대기중": 7,
        "대기접수": 6,
    },
    "district_audited_non_course_rows": 7,
    "district_current_rows": 101,
    "district_current_source_status_counts": {
        "접수마감": 74,
        "접수": 16,
        "대기중": 7,
        "대기접수": 4,
    },
    "lifelong_office": BUSAN_LIFELONG_HAEUNDAE_OFFICE,
    "lifelong_rows": 132,
    "lifelong_data_pages": 1,
    "lifelong_external_rows": 132,
    "lifelong_external_exact_district_rows": 130,
    "lifelong_external_audited_expired_tombstones": 2,
    "lifelong_native_rows": 0,
    "resident_url": BUSAN_CITY_HAEUNDAE_URL,
    "resident_rows": 83,
    "resident_data_pages": 9,
    "resident_page_counts": {"1-8": 10, "9": 3},
    "resident_sentinel_page": 10,
    "resident_current_rows": 83,
    "resident_status_counts": {"접수마감": 50, "접수중": 32, "대기접수": 1},
    "resident_branch_count": 8,
    "source_rows": 943,
    "duplicate_external_rows": 132,
    "unique_publishable_source_rows": 804,
    "atomic_current_rows": 184,
    "atomic_status_counts": {"OPEN": 53, "SCHEDULED": 7, "CLOSED": 124},
    "active_online_application_rows": 23,
    "required_list_requests": 65,
    "required_detail_requests": 184,
    "complete_network_requests": 249,
}


class BusanHaeundaeContractError(ValueError):
    """Raised when an audited Haeundae source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or status-200 gateway/error-page failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_RESNO_RE = re.compile(r"^20\d{8}$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_LOCAL_TOTAL_RE = re.compile(
    r"전체\s*([\d,]+)\s*,\s*현재\s*페이지\s*(\d+)\s*/\s*(\d+)"
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
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LOCAL_LIST_LABELS = (
    "교육기간",
    "신청기간",
    "교육시간",
    "모집인원",
    "교육장소",
)
_LOCAL_LIST_OPTIONAL_LABEL = "접수현황"
_LOCAL_STATUS_MAP = {
    "접수": "OPEN",
    "대기접수": "OPEN",
    "대기중": "SCHEDULED",
    "접수마감": "CLOSED",
}
_LOCAL_STATUS_CLASSES = {
    "접수": "ico1",
    "대기중": "ico2",
    "대기접수": "ico2",
    "접수마감": "ico3",
    "접수확인": "ico4",
}
_LOCAL_DETAIL_INFO_LABEL_VARIANTS = frozenset(
    {
        ("교육기간", "교육시간", "수강금액", "교육장소"),
        ("교육기간", "교육시간", "교육요일", "수강금액", "교육장소"),
    }
)
_LOCAL_DETAIL_STATUS_BY_LIST = {
    "접수": frozenset({"접수중", "대기접수중"}),
    "대기접수": frozenset({"대기접수중"}),
    "대기중": frozenset({"접수대기"}),
    "접수마감": frozenset({"접수마감"}),
}

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
        "교육대상",
        "교육장소",
        "교육기간",
        "교육시간",
        "수강료",
        "일반모집기간",
        "모집방법",
        "신청상태",
    }
)

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "대기접수": "OPEN",
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
_CITY_DETAIL_SAFE = frozenset(_CITY_DETAIL_REQUIRED) - {"문의전화"}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _one(values: Iterable[Any], label: str) -> Any:
    items = list(values)
    if len(items) != 1:
        raise BusanHaeundaeContractError(f"expected one {label}, got {len(items)}")
    return items[0]


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def _positive_int(value: Any, label: str) -> int:
    text = _clean(value)
    if not text.isdigit() or int(text) < 1:
        raise BusanHaeundaeContractError(f"invalid {label}")
    return int(text)


def _exact_query_url(value: Any, host: str, path: str) -> dict[str, list[str]]:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != host
        or parsed.port not in (None, 443)
        or parsed.path != path
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.params
    ):
        raise BusanHaeundaeContractError("response URL escaped exact source scope")
    return parse_qs(parsed.query, keep_blank_values=True)


def is_busan_haeundae_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_HAEUNDAE_PROVIDER:
        return False
    try:
        query = _exact_query_url(
            _target_value(target, "url"),
            BUSAN_HAEUNDAE_HOST,
            BUSAN_HAEUNDAE_PATH,
        )
    except BusanHaeundaeContractError:
        return False
    if query != {"menuCd": [BUSAN_HAEUNDAE_LIST_MENU]}:
        return False
    candidate = _clean(_target_value(target, "candidate_id"))
    return not candidate or candidate == BUSAN_HAEUNDAE_CANDIDATE_ID


is_target = is_busan_haeundae_education_target


def busan_haeundae_list_url(page: int = 1) -> str:
    page_number = _positive_int(page, "district page")
    return f"https://{BUSAN_HAEUNDAE_HOST}{BUSAN_HAEUNDAE_PATH}?" + urlencode(
        {"menuCd": BUSAN_HAEUNDAE_LIST_MENU, "page_no": page_number}
    )


def busan_haeundae_detail_url(identity: Any) -> str:
    token = _clean(identity)
    if not _RESNO_RE.fullmatch(token):
        raise BusanHaeundaeContractError("invalid Haeundae res_no")
    return f"https://{BUSAN_HAEUNDAE_HOST}{BUSAN_HAEUNDAE_PATH}?" + urlencode(
        {"menuCd": BUSAN_HAEUNDAE_DETAIL_MENU, "res_no": token}
    )


def busan_haeundae_city_list_url(page: int = 1) -> str:
    page_number = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", page_number),
            ("srchGugun", BUSAN_CITY_HAEUNDAE_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_haeundae_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _positive_int(group_id, "city group identity")
    program = _positive_int(program_id, "city program identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        {"resveGroupSn": group, "progrmSn": program}
    )


def busan_haeundae_lifelong_list_url(page: int = 1) -> str:
    page_number = _positive_int(page, "platform page")
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(
        {
            "display_type": "2",
            "pageUnit": BUSAN_LIFELONG_PAGE_SIZE,
            "l_search_ch": "0",
            "inst_id": BUSAN_LIFELONG_HAEUNDAE_OFFICE,
            "pageIndex": page_number,
        }
    )


def busan_haeundae_lifelong_detail_url(identity: Any) -> str:
    token = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(token):
        raise BusanHaeundaeContractError("invalid native platform identity")
    return _lifelong.busan_lifelong_detail_url(token)


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanHaeundaeContractError("invalid source date") from exc
    return result


def _date_pair(value: Any, label: str) -> tuple[str, str]:
    values = _dates(value)
    if len(values) != 2 or values[1] < values[0]:
        raise BusanHaeundaeContractError(f"invalid {label}")
    return values[0].isoformat(), values[1].isoformat()


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCen-Haeundae-Audit/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _close_quietly(value: Any) -> None:
    try:
        value.close()
    except Exception:
        pass


class _RequestBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self._lock = threading.Lock()

    def reserve(self) -> None:
        with self._lock:
            if self.count >= self.limit:
                raise BusanHaeundaeContractError("max_requests cap exhausted")
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise _TransientFetchError(f"HTTP {status}")
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        content = _clean(getattr(response, "text", "")).encode("utf-8")
    if not content or len(content) > BUSAN_HAEUNDAE_MAX_HTML_BYTES:
        raise _TransientFetchError("empty or oversized HTML")
    soup = BeautifulSoup(bytes(content), "lxml")
    plain = _clean(soup.get_text(" ", strip=True)).casefold()
    if (
        len(content) < 2048
        and any(
            token in plain
            for token in (
                "bad request",
                "service unavailable",
                "temporarily unavailable",
                "internal server error",
            )
        )
    ):
        raise _TransientFetchError("status-200 transient error page")
    return soup, _clean(getattr(response, "url", "") or requested_url)


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
    messages: list[str] = []
    sessions = 0
    for attempt in range(1, BUSAN_HAEUNDAE_FETCH_ATTEMPTS + 1):
        session = None
        try:
            budget.reserve()
            session = session_factory()
            sessions += 1
            response = fetcher(session, url, timeout)
            soup, final_url = _response_soup(response, url)
            value = parser(soup, final_url)
            return _FetchResult(value, attempt - 1, sessions)
        except BusanHaeundaeContractError:
            raise
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
            if attempt < BUSAN_HAEUNDAE_FETCH_ATTEMPTS:
                sleeper(min(0.25 * attempt, 0.75))
        finally:
            if session is not None:
                _close_quietly(session)
    raise BusanHaeundaeContractError("; ".join(messages))


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
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(items)))) as pool:
        future_map = {
            pool.submit(
                _fetch_parsed,
                url,
                parser,
                fetcher=fetcher,
                session_factory=session_factory,
                timeout=timeout,
                sleeper=sleeper,
                budget=budget,
            ): key
            for key, url, parser in items
        }
        for future in as_completed(future_map):
            result = future.result()
            key = future_map[future]
            if key in values:
                raise BusanHaeundaeContractError("duplicate fetch key")
            values[key] = result.value
            retries += result.retries
            sessions += result.sessions
    if len(values) != len(items):
        raise BusanHaeundaeContractError("incomplete concurrent fetch")
    return values, retries, sessions


def _direct_label(node: Tag) -> str:
    for child in node.children:
        if isinstance(child, NavigableString):
            direct = _clean(child)
            if direct:
                return _clean(direct.split(":", 1)[0])
    return ""


def _local_period(value: str, identity: str, label: str) -> tuple[str, str, bool]:
    values = _dates(value)
    if len(values) == 2:
        start, end = sorted(values)
        return start.isoformat(), end.isoformat(), False
    if not values and _clean(value) == "연중":
        year = int(identity[:4])
        return f"{year:04d}-01-01", f"{year:04d}-12-31", True
    raise BusanHaeundaeContractError(
        f"invalid district {label} for {identity}: {_clean(value)!r}"
    )


def _local_list_contract(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[int, int, Optional[Tag]]:
    query = _exact_query_url(final_url, BUSAN_HAEUNDAE_HOST, BUSAN_HAEUNDAE_PATH)
    if query != {
        "menuCd": [BUSAN_HAEUNDAE_LIST_MENU],
        "page_no": [str(page)],
    }:
        raise BusanHaeundaeContractError("district list response query changed")
    if _text(_one(soup.select("title"), "district list title")) != "전체 | 해운대구청":
        raise BusanHaeundaeContractError("district list title changed")
    form = _one(soup.select("form#searchForm[name='searchForm']"), "district search form")
    if _clean(form.get("method")).casefold() != "get" or _clean(form.get("action")):
        raise BusanHaeundaeContractError("district search form changed")
    menu = _one(form.select("input[name='menuCd']"), "district menu field")
    if _clean(menu.get("value")) != BUSAN_HAEUNDAE_LIST_MENU:
        raise BusanHaeundaeContractError("district search menu changed")
    legacy_page = _one(form.select("input[name='paga_no']"), "district legacy page field")
    if _clean(legacy_page.get("value")) != "1":
        raise BusanHaeundaeContractError("district legacy page field changed")
    summary = _text(_one(soup.select("form#searchForm p.articles"), "district summary"))
    match = _LOCAL_TOTAL_RE.fullmatch(summary)
    if not match:
        raise BusanHaeundaeContractError("district pagination summary changed")
    total = int(match.group(1).replace(",", ""))
    current_page = int(match.group(2))
    last = int(match.group(3))
    if current_page != page or last != max(1, math.ceil(total / 16)):
        raise BusanHaeundaeContractError("district pagination arithmetic changed")
    if expected_total is not None and total != expected_total:
        raise BusanHaeundaeContractError("district total changed during census")
    if expected_last is not None and last != expected_last:
        raise BusanHaeundaeContractError("district final page changed during census")
    sections = soup.select("section.reserV")
    root = _one(sections, "district course section") if sections else None
    if page <= last and root is None:
        raise BusanHaeundaeContractError("district data page lost course section")
    if page > last + 1:
        raise BusanHaeundaeContractError("district request passed sentinel")
    return total, last, root


def _parse_local_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last, root = _local_list_contract(
        soup,
        final_url,
        page=page,
        expected_total=expected_total,
        expected_last=expected_last,
    )
    cards = root.find_all("div", class_="reserVbox", recursive=False) if root else []
    expected_count = 0 if page == last + 1 else min(16, total - (page - 1) * 16)
    if len(cards) != expected_count:
        raise BusanHaeundaeContractError("district page row count changed")
    rows: list[dict[str, Any]] = []
    for position, card in enumerate(cards, 1):
        classes = set(card.get("class", []))
        if "clearfix" not in classes:
            raise BusanHaeundaeContractError("district card class changed")
        card_id = _clean(card.get("id"))
        if not card_id.startswith("ae") or not _RESNO_RE.fullmatch(card_id[2:]):
            raise BusanHaeundaeContractError("district card identity changed")
        identity = card_id[2:]
        base = _one(card.select(":scope > div.base"), "district card base")
        title = _text(_one(base.select("strong.title"), "district title"))
        if not title:
            raise BusanHaeundaeContractError("empty district title")
        detail_links: list[Tag] = []
        for link in card.select("a[href]"):
            parsed = urlparse(urljoin(BUSAN_HAEUNDAE_CANONICAL_URL, _clean(link.get("href"))))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if query.get("res_no") == [identity]:
                if (
                    parsed.scheme != "https"
                    or (parsed.hostname or "").lower() != BUSAN_HAEUNDAE_HOST
                    or parsed.path != BUSAN_HAEUNDAE_PATH
                    or query.get("menuCd") != [BUSAN_HAEUNDAE_DETAIL_MENU]
                ):
                    raise BusanHaeundaeContractError("unsafe district detail link")
                detail_links.append(link)
        if not detail_links:
            raise BusanHaeundaeContractError("district card lacks detail link")
        # The legacy template's optional ``title`` attribute is lossy: it
        # removes the audited ``(추첨)`` suffix, collapses author-entered double
        # spaces and truncates three old titles at an embedded quote.  The
        # visible ``strong.title`` plus exact ``res_no`` link is authoritative.

        items = base.select(":scope ul > li")
        labels = tuple(_direct_label(item) for item in items)
        if labels not in (
            _LOCAL_LIST_LABELS,
            _LOCAL_LIST_LABELS + (_LOCAL_LIST_OPTIONAL_LABEL,),
        ):
            raise BusanHaeundaeContractError("district card field order changed")
        safe_values: dict[str, str] = {}
        for item, label in zip(items, labels):
            if label == _LOCAL_LIST_OPTIONAL_LABEL:
                continue
            whole = _text(item)
            prefix = whole.split(":", 1)
            if len(prefix) != 2 or _clean(prefix[0]) != label:
                raise BusanHaeundaeContractError("district card field changed")
            safe_values[label] = _clean(prefix[1])
        if not safe_values.get("교육기간") or not safe_values.get("신청기간"):
            raise BusanHaeundaeContractError("empty district date field")
        start, end, annual_period = _local_period(
            safe_values["교육기간"], identity, "education period"
        )
        apply_start, apply_end, annual_apply = _local_period(
            safe_values["신청기간"], identity, "application period"
        )
        source_education_dates = _dates(safe_values["교육기간"])
        source_application_dates = _dates(safe_values["신청기간"])
        status_nodes = card.select(":scope > div.btn_reserv span.head")
        status_values = [_text(node) for node in status_nodes]
        primary = [value for value in status_values if value != "접수확인"]
        if len(primary) != 1 or primary[0] not in _LOCAL_STATUS_MAP:
            raise BusanHaeundaeContractError("district card status changed")
        for node, value in zip(status_nodes, status_values):
            if _LOCAL_STATUS_CLASSES.get(value) not in set(node.get("class", [])):
                raise BusanHaeundaeContractError("district card status class changed")
        source_status = primary[0]
        rows.append(
            {
                "provider": BUSAN_HAEUNDAE_PROVIDER,
                "provider_course_id": f"{BUSAN_HAEUNDAE_PROVIDER}:district:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": BUSAN_HAEUNDAE_MUNICIPALITY_NAME,
                "branch_code": BUSAN_HAEUNDAE_MUNICIPALITY_CODE,
                "preserve_branch": True,
                "provider_organizer": "해운대구청",
                "category": "통합예약 교육",
                "program_type": "교육/강좌",
                "raw_url": busan_haeundae_detail_url(identity),
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
                "schedule_raw": safe_values["교육시간"],
                "fee": "",
                "capacity": safe_values["모집인원"],
                "target": "",
                "venue_name": safe_values["교육장소"],
                "municipality_code": BUSAN_HAEUNDAE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_HAEUNDAE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "해운대구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_HAEUNDAE_PARSER,
                    "source_catalog": "haeundae_complete_district_education",
                    "source_identity": identity,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_annual_education_period": annual_period,
                    "source_annual_application_period": annual_apply,
                    "source_reversed_education_period": bool(
                        len(source_education_dates) == 2
                        and source_education_dates[1] < source_education_dates[0]
                    ),
                    "source_reversed_application_period": bool(
                        len(source_application_dates) == 2
                        and source_application_dates[1] < source_application_dates[0]
                    ),
                    "reception_values_never_read": (
                        _LOCAL_LIST_OPTIONAL_LABEL in labels
                    ),
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "applicant_list_fetched": False,
                    "service_family": "education",
                },
            }
        )
    return rows, total, last


def _signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _parse_local_detail(
    soup: BeautifulSoup,
    final_url: str,
    parent: Mapping[str, Any],
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    query = _exact_query_url(final_url, BUSAN_HAEUNDAE_HOST, BUSAN_HAEUNDAE_PATH)
    if query != {
        "menuCd": [BUSAN_HAEUNDAE_DETAIL_MENU],
        "res_no": [identity],
    }:
        raise BusanHaeundaeContractError("district detail response identity changed")
    if _text(_one(soup.select("title"), "district detail title")) != "강좌 상세 | 해운대구청":
        raise BusanHaeundaeContractError("district detail page title changed")
    detail_title = _text(_one(soup.select("h1#tit_cont"), "district course heading"))
    if not _platform_title_matches(_clean(parent.get("title")), detail_title):
        raise BusanHaeundaeContractError("district list/detail title mismatch")
    identity_fields = {
        _clean(node.get("value"))
        for node in soup.select("form[name='frm'] input[name='res_no']")
    }
    if identity_fields != {identity}:
        raise BusanHaeundaeContractError("district detail hidden identity changed")
    root = _one(soup.select("div.reserWrap > ul"), "district safe detail root")
    blocks = root.find_all("li", recursive=False)
    if len(blocks) != 4:
        raise BusanHaeundaeContractError("district detail block count changed")
    headings = [_text(_one(block.select(":scope > h4"), "district block heading")) for block in blocks]
    if (
        headings[0] != "신청기간"
        or headings[1] not in {"신청인원", "신청인원 (추첨승인제)"}
        or headings[2] != "교육정보"
        or headings[3] not in _LOCAL_DETAIL_STATUS_BY_LIST[_clean(raw.get("source_status"))]
    ):
        raise BusanHaeundaeContractError("district detail block headings changed")
    apply_value = _text(
        _one(blocks[0].select(":scope > div.cont"), "district application period")
    )
    apply_start, apply_end = _date_pair(apply_value, "district detail application period")
    if (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanHaeundaeContractError("district list/detail application dates mismatch")
    info_rows = blocks[2].select(":scope > ul > li")
    labels = tuple(_text(_one(row.select(":scope > span"), "district detail label")) for row in info_rows)
    if labels not in _LOCAL_DETAIL_INFO_LABEL_VARIANTS:
        raise BusanHaeundaeContractError("district detail education fields changed")
    safe: dict[str, str] = {}
    for row, label in zip(info_rows, labels):
        clone = BeautifulSoup(str(row), "lxml")
        _one(clone.select("span"), "cloned district label").extract()
        safe[label] = _text(clone)
    if not safe.get("교육기간"):
        raise BusanHaeundaeContractError(
            f"empty district safe detail value for {identity}"
        )
    start, end = _date_pair(safe["교육기간"], "district detail education period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanHaeundaeContractError("district list/detail education dates mismatch")
    controls = blocks[3].select(":scope > div.cont > a")
    if len(controls) > 1:
        raise BusanHaeundaeContractError("multiple district application controls")
    detail_status = headings[3]
    active = detail_status in {"접수중", "대기접수중"}
    control_label = _text(controls[0]) if controls else ""
    if active:
        if len(controls) != 1 or control_label != "신청하기":
            raise BusanHaeundaeContractError("active district course lacks exact control")
        parsed = urlparse(urljoin(BUSAN_HAEUNDAE_CANONICAL_URL, _clean(controls[0].get("href"))))
        control_query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != BUSAN_HAEUNDAE_HOST
            or parsed.path != BUSAN_HAEUNDAE_PATH
            or control_query.get("menuCd") != [BUSAN_HAEUNDAE_APPLY_MENU]
            or control_query.get("res_no") != [identity]
        ):
            raise BusanHaeundaeContractError("district application control escaped identity")
    elif detail_status == "접수대기":
        if len(controls) != 1 or control_label != "접수대기" or _clean(controls[0].get("href")) != "#":
            raise BusanHaeundaeContractError("scheduled district control changed")
    elif len(controls) == 1:
        parsed = urlparse(
            urljoin(BUSAN_HAEUNDAE_CANONICAL_URL, _clean(controls[0].get("href")))
        )
        control_query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            control_label != "접수확인"
            or parsed.scheme != "https"
            or (parsed.hostname or "").lower() != BUSAN_HAEUNDAE_HOST
            or parsed.path != BUSAN_HAEUNDAE_PATH
            or control_query != {
                "menuCd": [BUSAN_HAEUNDAE_MY_RESERVATION_MENU]
            }
        ):
            raise BusanHaeundaeContractError("closed district control changed")
    if len(soup.select("div.reserCont")) != 1:
        raise BusanHaeundaeContractError("district free-form boundary changed")
    source_fee = safe["수강금액"]
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY"
                if detail_status == "대기접수중"
                else "ONLINE_RESERVATION" if active else "INFO_ONLY"
            ),
            "reservation_available": active,
            "status": (
                "OPEN"
                if active
                else "SCHEDULED" if detail_status == "접수대기" else "CLOSED"
            ),
            "schedule_raw": safe["교육시간"] or _clean(parent.get("schedule_raw")),
            "fee": source_fee or "요금 별도 안내",
            "target": "대상 별도 안내",
            "venue_name": safe["교육장소"] or _clean(parent.get("venue_name")),
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": detail_status,
        "detail_application_control": control_label,
        "detail_education_weekday": safe.get("교육요일", ""),
        "target_evidence": "official_district_detail_omits_target_field",
        "fee_evidence": (
            "official_district_detail"
            if source_fee
            else "official_district_detail_empty_fee"
        ),
        "enrollment_value_never_read": True,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    # This local excluded-owner object lets the dedicated collector run before
    # and after the root shared registry transfers OFFICE_00002635 ownership.
    return _lifelong.BusanOffice(
        BUSAN_LIFELONG_HAEUNDAE_OFFICE,
        BUSAN_LIFELONG_HAEUNDAE_OFFICE_NAME,
        ownership="duplicate_dedicated_haeundae_owner",
    )


def _parse_platform_page(
    soup: BeautifulSoup,
    _final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    office = _platform_office()
    errors = _lifelong._form_errors(soup, office, page)
    if errors:
        raise BusanHaeundaeContractError("; ".join(errors))
    last, errors = _lifelong._advertised_last(soup)
    if errors:
        raise BusanHaeundaeContractError("; ".join(errors))
    if expected_last is not None and last != expected_last:
        raise BusanHaeundaeContractError("platform final page changed")
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if errors:
        raise BusanHaeundaeContractError("; ".join(errors))
    if page <= last and not rows:
        raise BusanHaeundaeContractError("platform data page became empty")
    if page == last + 1 and rows:
        raise BusanHaeundaeContractError("platform sentinel is not empty")
    if page > last + 1:
        raise BusanHaeundaeContractError("platform request passed sentinel")
    return rows, last


def _platform_semantic_multiset(
    rows: Sequence[Mapping[str, Any]],
) -> Counter[tuple[str, ...]]:
    return Counter(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )


def _platform_external_resno(row: Mapping[str, Any]) -> str:
    raw = row.get("raw_fields", {})
    if _clean(raw.get("identity_kind")) != "external":
        raise BusanHaeundaeContractError("platform row is not external")
    parsed = urlparse(_clean(row.get("raw_url")))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = _clean((query.get("res_no") or [""])[0])
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != BUSAN_HAEUNDAE_HOST
        or parsed.port not in (None, 443)
        or parsed.path != BUSAN_HAEUNDAE_PATH
        or parsed.fragment
        or parsed.username
        or parsed.password
        or query.get("menuCd") != [BUSAN_HAEUNDAE_PLATFORM_DETAIL_MENU]
        or len(query.get("res_no", [])) != 1
        or not _RESNO_RE.fullmatch(identity)
    ):
        raise BusanHaeundaeContractError("platform external row left Haeundae scope")
    return identity


def _platform_title_matches(district_title: str, platform_title: str) -> bool:
    if district_title == platform_title:
        return True
    return district_title.endswith("(추첨)") and district_title.removesuffix(
        "(추첨)"
    ).rstrip() == platform_title


def _prove_platform_duplicate(
    row: Mapping[str, Any], district_by_id: Mapping[str, Mapping[str, Any]]
) -> str:
    identity = _platform_external_resno(row)
    district = district_by_id.get(identity)
    if district is None:
        expected = _AUDITED_PLATFORM_TOMBSTONES.get(identity)
        observed = (
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
        )
        if expected != observed:
            raise BusanHaeundaeContractError(
                "unknown platform external row absent from district census"
            )
        return identity
    if not _platform_title_matches(
        _clean(district.get("title")), _clean(row.get("title"))
    ) or (
        _clean(district.get("start_date")),
        _clean(district.get("end_date")),
    ) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        raise BusanHaeundaeContractError(
            "platform external row does not exactly prove district ownership"
        )
    district_apply = (
        _clean(district.get("apply_start")),
        _clean(district.get("apply_end")),
    )
    platform_apply = (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    )
    if district_apply != platform_apply:
        expected_drift = _AUDITED_PLATFORM_APPLY_END_DRIFT.get(identity)
        if (
            expected_drift
            != (platform_apply[1], district_apply[1])
            or platform_apply[0] != district_apply[0]
        ):
            raise BusanHaeundaeContractError(
                "platform application-date drift changed"
            )
    return identity


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(identity):
        raise BusanHaeundaeContractError("invalid native platform identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_HAEUNDAE_PROVIDER,
            "provider_course_id": f"{BUSAN_HAEUNDAE_PROVIDER}:lifelong:{identity}",
            "prefer_incoming_provider_course_id": True,
            "branch": BUSAN_LIFELONG_HAEUNDAE_OFFICE_NAME,
            "branch_code": "haeundae-lifelong-office00002635",
            "preserve_branch": True,
            "provider_organizer": BUSAN_LIFELONG_HAEUNDAE_OFFICE_NAME,
            "municipality_code": BUSAN_HAEUNDAE_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_HAEUNDAE_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "해운대구",
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
        "parser": BUSAN_HAEUNDAE_PARSER,
        "source_catalog": "busan_lifelong_haeundae_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_detail_values(
    soup: BeautifulSoup,
) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in soup.select("div.form_group dl"):
        label = _text(
            _one(definition.find_all("dt", recursive=False), "platform detail label")
        )
        value = _one(
            definition.find_all("dd", recursive=False), "platform detail value"
        )
        if label in labels:
            raise BusanHaeundaeContractError("duplicate platform detail field")
        labels.append(label)
        if label in _PLATFORM_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = tuple(label for label in labels if label not in _PLATFORM_OPTIONAL_LABELS)
    if required != _PLATFORM_DETAIL_REQUIRED:
        raise BusanHaeundaeContractError("platform detail fields changed")
    return labels, safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    query = _exact_query_url(
        final_url, _lifelong.BUSAN_LIFELONG_HOST, BUSAN_LIFELONG_DETAIL_PATH
    )
    if query != {"lng_id": [identity]}:
        raise BusanHaeundaeContractError("platform detail identity changed")
    for name, expected in (
        ("lng_id", identity),
        ("inst_id", BUSAN_LIFELONG_HAEUNDAE_OFFICE),
    ):
        values = {
            _clean(node.get("value"))
            for node in soup.select(f"input[name='{name}']")
        }
        if values != {expected}:
            raise BusanHaeundaeContractError(f"platform detail {name} changed")
    heading = _one(soup.select("h2.enrolTit"), "platform heading")
    if _text(_one(heading.select(":scope > span"), "platform office prefix")) != (
        f"[{BUSAN_LIFELONG_HAEUNDAE_OFFICE_NAME}]"
    ):
        raise BusanHaeundaeContractError("platform detail office changed")
    direct_title = _clean(
        " ".join(
            str(child) for child in heading.children if isinstance(child, NavigableString)
        )
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanHaeundaeContractError("platform list/detail title mismatch")
    labels, safe, skipped = _platform_detail_values(soup)
    if any(not safe.get(label) for label in _PLATFORM_SAFE_LABELS if label in labels):
        raise BusanHaeundaeContractError("empty platform safe detail value")
    start, end = _date_pair(safe["교육기간"], "platform education period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanHaeundaeContractError("platform list/detail dates mismatch")
    controls = soup.select("#learning_aply_btn")
    if len(controls) > 1:
        raise BusanHaeundaeContractError("multiple platform controls")
    control_label = _text(controls[0]) if controls else ""
    apply_status = safe["신청상태"]
    active = bool(
        len(controls) == 1
        and "접수중" in apply_status
        and _clean(controls[0].get("onclick")) == "fn_learning_apply(); return false;"
        and control_label in {"일반모집신청", "대기자신청", "우선모집신청"}
    )
    if controls and not active:
        raise BusanHaeundaeContractError("platform control/status changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY"
                if active and control_label == "대기자신청"
                else "ONLINE_RESERVATION" if active else "INFO_ONLY"
            ),
            "reservation_available": active,
            "status": (
                "OPEN"
                if active
                else "SCHEDULED" if "접수대기" in apply_status else "CLOSED"
            ),
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
        "detail_source_status": apply_status,
        "detail_application_control": control_label,
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
    query = _exact_query_url(final_url, BUSAN_CITY_HOST, BUSAN_CITY_LIST_PATH)
    if query != {
        "curPage": [str(page)],
        "srchGugun": [BUSAN_CITY_HAEUNDAE_GUGUN],
        "srchResveInsttCd": [BUSAN_CITY_RESIDENT_OFFICE],
    }:
        raise BusanHaeundaeContractError("city list response query changed")
    if _text(_one(soup.select("title"), "city list title")) != _CITY_LIST_TITLE:
        raise BusanHaeundaeContractError("city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanHaeundaeContractError("city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "city page field")
    if _clean(page_field.get("value")) != str(page):
        raise BusanHaeundaeContractError("city form page changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_HAEUNDAE_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanHaeundaeContractError(f"city {name} filter changed")
    end = _one(soup.select("div.paginate > a.pgEnd[href]"), "city final page")
    parsed = urlparse(urljoin(BUSAN_CITY_HAEUNDAE_URL, _clean(end.get("href"))))
    end_query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        (parsed.hostname or "").lower() != BUSAN_CITY_HOST
        or parsed.path != BUSAN_CITY_LIST_PATH
        or set(end_query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or end_query.get("srchGugun") != [BUSAN_CITY_HAEUNDAE_GUGUN]
        or end_query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
        or len(end_query.get("curPage", [])) != 1
        or not end_query["curPage"][0].isdigit()
    ):
        raise BusanHaeundaeContractError("unsafe city final-page control")
    last = int(end_query["curPage"][0])
    roots = soup.select("ul.reserveList")
    if page <= last:
        return last, _one(roots, "city reserve list")
    if page == last + 1:
        if roots:
            raise BusanHaeundaeContractError("city sentinel is not empty")
        return last, None
    raise BusanHaeundaeContractError("city request passed sentinel")


def _city_date_ranges(value: Any) -> tuple[str, str, str, str]:
    match = _CITY_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanHaeundaeContractError("city card dates changed")
    values = [date.fromisoformat(item).isoformat() for item in match.groups()]
    if values[1] < values[0] or values[3] < values[2]:
        raise BusanHaeundaeContractError("city card date range reversed")
    return values[0], values[1], values[2], values[3]


def _parse_city_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(soup, final_url, page=page)
    if expected_last is not None and last != expected_last:
        raise BusanHaeundaeContractError("city final page changed")
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(root.find_all("li", recursive=False) if root else [], 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "city course link")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanHaeundaeContractError("city identity action changed")
        group_id, program_id = action.groups()
        identity = f"{group_id}:{program_id}"
        title_node = _one(link.select(":scope .tit"), "city title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        if not title or (
            title_attribute != title
            and _AUDITED_CITY_TITLE_ATTRIBUTE.get(identity)
            != (title, title_attribute)
        ):
            raise BusanHaeundaeContractError("city title attribute changed")
        source_status = _text(_one(link.select(":scope .statusMark"), "city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanHaeundaeContractError("unknown city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanHaeundaeContractError("city card labels changed")
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanHaeundaeContractError("empty city safe card value")
        branch = safe["기관"]
        if not branch.startswith("해운대구 ") or not branch.endswith(" 주민자치회"):
            raise BusanHaeundaeContractError("city course left Haeundae owner")
        apply_start, apply_end, start, end = _city_date_ranges(safe["일자"])
        method = ", ".join(
            part for part in (_clean(part) for part in safe["방법"].split(",")) if part
        )
        if method != safe["방법"]:
            raise BusanHaeundaeContractError("city application method changed")
        rows.append(
            {
                "provider": BUSAN_HAEUNDAE_PROVIDER,
                "provider_course_id": f"{BUSAN_HAEUNDAE_PROVIDER}:reserve:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"haeundae-reserve-{group_id}",
                "preserve_branch": True,
                "provider_organizer": branch,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": busan_haeundae_city_detail_url(group_id, program_id),
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
                "municipality_code": BUSAN_HAEUNDAE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_HAEUNDAE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "해운대구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_HAEUNDAE_PARSER,
                    "source_catalog": "busan_reserve_haeundae_resident_councils",
                    "source_identity": identity,
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": method,
                    "source_card_dates": safe["일자"],
                    "audited_title_attribute_anomaly": identity in _AUDITED_CITY_TITLE_ATTRIBUTE,
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
        label = _text(
            _one(definition.find_all("dt", recursive=False), "city detail label")
        )
        value = _one(definition.find_all("dd", recursive=False), "city detail value")
        if label in labels:
            raise BusanHaeundaeContractError("duplicate city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE:
            safe[label] = _text(value)
        elif label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        else:
            raise BusanHaeundaeContractError(f"unknown city detail field {label!r}")
    without_attachment = tuple(label for label in labels if label != "첨부파일")
    if without_attachment != _CITY_DETAIL_REQUIRED or "문의전화" not in skipped:
        raise BusanHaeundaeContractError("city detail field order changed")
    return labels, safe, skipped


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    query = _exact_query_url(final_url, BUSAN_CITY_HOST, BUSAN_CITY_DETAIL_PATH)
    if query != {"resveGroupSn": [group_id], "progrmSn": [program_id]}:
        raise BusanHaeundaeContractError("city detail response identity changed")
    if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
        raise BusanHaeundaeContractError("city detail title changed")
    form = _one(soup.select("form#viewForm"), "city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanHaeundaeContractError("city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanHaeundaeContractError("city detail hidden identity changed")
    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city heading")
    source_status = _text(_one(heading.select(":scope .statusMark"), "city status"))
    direct_title = _clean(
        " ".join(
            str(child) for child in heading.children if isinstance(child, NavigableString)
        )
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanHaeundaeContractError("city list/detail title mismatch")
    list_status = _clean(raw.get("source_status"))
    expected_detail_status = "대기자접수" if list_status == "대기접수" else list_status
    if source_status != expected_detail_status:
        raise BusanHaeundaeContractError("city list/detail status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "city safe detail values",
    )
    _labels, safe, skipped = _city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE):
        raise BusanHaeundaeContractError("empty city safe detail value")
    start, end = _date_pair(safe["운영기간"], "city operating period")
    apply_start, apply_end = _date_pair(safe["신청기간"], "city application period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanHaeundaeContractError("city list/detail dates mismatch")
    if (
        safe["신청방법"] != _clean(raw.get("source_application_method"))
        or safe["운영기관"] != _clean(parent.get("branch"))
        or safe["대상"] != _clean(parent.get("target"))
    ):
        raise BusanHaeundaeContractError("city list/detail safe values mismatch")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanHaeundaeContractError("city free-form boundary changed")
    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanHaeundaeContractError("multiple city application controls")
    label = _text(controls[0]) if controls else ""
    status = _CITY_STATUS_MAP[list_status]
    method = safe["신청방법"]
    online = "온라인" in method
    active = status == "OPEN" and online
    if active and (len(controls) != 1 or label not in {"예약하기", "대기예약"}):
        raise BusanHaeundaeContractError("online city course lacks exact control")
    if status == "OPEN" and not online and (
        len(controls) != 1 or label != "방문예약"
    ):
        raise BusanHaeundaeContractError("offline city control changed")
    if status == "CLOSED" and label not in {"", "접수마감"}:
        raise BusanHaeundaeContractError("closed city control changed")
    if status == "SCHEDULED" and label not in {"", "대기중", "접수대기"}:
        raise BusanHaeundaeContractError("scheduled city control changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY"
                if active and list_status == "대기접수"
                else "ONLINE_RESERVATION"
                if active
                else "OFFLINE_APPLY" if status == "OPEN" else "INFO_ONLY"
            ),
            "reservation_available": active,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": source_status,
        "detail_application_control": label,
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
            "contact",
        )
    )


def _scrub_text(value: Any) -> tuple[str, int]:
    text = _clean(value)
    updated, first = _PHONE_RE.subn("[redacted]", text)
    updated, second = _EMAIL_RE.subn("[redacted]", updated)
    return updated, first + second


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def walk(value: Any, key: str = "") -> Any:
        nonlocal redactions
        if _pii_key(key):
            redactions += 1
            return None
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for child_key, child_value in value.items():
                cleaned = walk(child_value, _clean(child_key))
                if cleaned is not None:
                    result[str(child_key)] = cleaned
            return result
        if isinstance(value, (list, tuple, set)):
            return [item for child in value if (item := walk(child, key)) is not None]
        if isinstance(value, str):
            updated, count = _scrub_text(value)
            redactions += count
            return updated
        return value

    sanitized = walk(dict(row))
    if not isinstance(sanitized, dict):
        raise BusanHaeundaeContractError("row sanitizer changed type")
    return sanitized, redactions


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "provider": BUSAN_HAEUNDAE_PROVIDER,
        "candidate_id": BUSAN_HAEUNDAE_CANDIDATE_ID,
        "canonical_url": BUSAN_HAEUNDAE_CANONICAL_URL,
        "registered_url": BUSAN_HAEUNDAE_REGISTERED_URL,
        "municipality_code": BUSAN_HAEUNDAE_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_HAEUNDAE_MUNICIPALITY_NAME,
        "ownership_scope": BUSAN_HAEUNDAE_OWNERSHIP_SCOPE,
        "discovery_audit": dict(BUSAN_HAEUNDAE_DISCOVERY_AUDIT),
        "pages": 0,
        "detail_pages": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
    }


def collect_busan_haeundae_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 80,
    detail_limit: int = 240,
    max_requests: int = 320,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_HAEUNDAE_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future Haeundae education snapshot."""

    meta = _base_meta()
    if not is_busan_haeundae_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Haeundae education owner"
        )
        return [], BUSAN_HAEUNDAE_PARSER, meta
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
        workers = min(max(1, int(max_workers)), BUSAN_HAEUNDAE_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_HAEUNDAE_PARSER, meta
    if page_cap < 1 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps cannot inspect all three ledgers"
        return [], BUSAN_HAEUNDAE_PARSER, meta

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

    def fetch_one(
        url: str,
        parser: Callable[[BeautifulSoup, str], Any],
        *,
        list_phase: bool,
    ) -> Any:
        return account(
            _fetch_parsed(
                url,
                parser,
                fetcher=fetch,
                session_factory=factory,
                timeout=request_timeout,
                sleeper=sleeper,
                budget=budget,
            ),
            list_phase=list_phase,
        )

    def fetch_batch(
        items: Sequence[tuple[Any, str, Callable[[BeautifulSoup, str], Any]]],
        *,
        list_phase: bool,
    ) -> dict[Any, Any]:
        if not items:
            return {}
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
        # Complete district education owner.
        first_rows, local_total, local_last = fetch_one(
            busan_haeundae_list_url(1),
            lambda soup, final: _parse_local_page(soup, final, page=1),
            list_phase=True,
        )
        if local_last > page_cap:
            raise BusanHaeundaeContractError(
                f"max_pages cap allows {page_cap} of {local_last} district pages"
            )
        local_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
        local_pages.update(
            fetch_batch(
                [
                    (
                        page,
                        busan_haeundae_list_url(page),
                        lambda soup, final, p=page: _parse_local_page(
                            soup,
                            final,
                            page=p,
                            expected_total=local_total,
                            expected_last=local_last,
                        )[0],
                    )
                    for page in range(2, local_last + 1)
                ],
                list_phase=True,
            )
        )
        sentinel, _, _ = fetch_one(
            busan_haeundae_list_url(local_last + 1),
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
        if sentinel:
            raise BusanHaeundaeContractError("district sentinel returned rows")
        boundary_pages = sorted({1, local_last})
        rechecked = fetch_batch(
            [
                (
                    page,
                    busan_haeundae_list_url(page),
                    lambda soup, final, p=page: _parse_local_page(
                        soup,
                        final,
                        page=p,
                        expected_total=local_total,
                        expected_last=local_last,
                    )[0],
                )
                for page in boundary_pages
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(boundary_pages)
        for page in boundary_pages:
            if _signature(rechecked[page]) != _signature(local_pages[page]):
                raise BusanHaeundaeContractError("district boundary page changed")
        local_rows = [
            row for page in range(1, local_last + 1) for row in local_pages[page]
        ]
        if len(local_rows) != local_total:
            raise BusanHaeundaeContractError("district complete count changed")
        local_by_id: dict[str, dict[str, Any]] = {}
        for row in local_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            if identity in local_by_id:
                raise BusanHaeundaeContractError("duplicate district res_no")
            local_by_id[identity] = row
        excluded_rows: list[dict[str, Any]] = []
        publishable_local: list[dict[str, Any]] = []
        exclusion_counts: Counter[str] = Counter()
        for identity, row in local_by_id.items():
            exclusion = _AUDITED_LOCAL_EXCLUSIONS.get(identity)
            if exclusion is None:
                publishable_local.append(row)
                continue
            expected_title, reason = exclusion
            if _clean(row.get("title")) != expected_title:
                raise BusanHaeundaeContractError(
                    "audited district non-course identity changed title"
                )
            excluded_rows.append(row)
            exclusion_counts[reason] += 1

        # Two identical complete platform censuses, each with a sentinel.
        platform_censuses: list[list[dict[str, Any]]] = []
        platform_last = 0
        for census_index in range(2):
            rows, current_last = fetch_one(
                busan_haeundae_lifelong_list_url(1),
                lambda soup, final: _parse_platform_page(soup, final, page=1),
                list_phase=True,
            )
            if current_last > page_cap:
                raise BusanHaeundaeContractError(
                    f"max_pages cap allows {page_cap} of {current_last} platform pages"
                )
            if current_last != 1:
                raise BusanHaeundaeContractError(
                    "pageUnit1000 no longer contains complete platform census"
                )
            empty, sentinel_last = fetch_one(
                busan_haeundae_lifelong_list_url(2),
                lambda soup, final: _parse_platform_page(
                    soup, final, page=2, expected_last=current_last
                ),
                list_phase=True,
            )
            meta["sentinel_requests"] += 1
            if empty or sentinel_last != current_last:
                raise BusanHaeundaeContractError("platform sentinel changed")
            if census_index:
                meta["stability_rechecks"] += 2
            platform_censuses.append(rows)
            platform_last = current_last
        if _platform_semantic_multiset(platform_censuses[0]) != (
            _platform_semantic_multiset(platform_censuses[1])
        ):
            raise BusanHaeundaeContractError("platform complete censuses changed")
        platform_rows = platform_censuses[0]
        sequences = sorted(
            int(row.get("raw_fields", {}).get("list_sequence") or 0)
            for row in platform_rows
        )
        if sequences != list(range(1, len(platform_rows) + 1)):
            raise BusanHaeundaeContractError("platform sequence changed")
        external_rows = [
            row
            for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "external"
        ]
        native_source = [
            row
            for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "internal"
        ]
        if len(external_rows) + len(native_source) != len(platform_rows):
            raise BusanHaeundaeContractError("unexpected platform identity family")
        external_ids = [
            _prove_platform_duplicate(row, local_by_id) for row in external_rows
        ]
        if len(external_ids) != len(set(external_ids)):
            raise BusanHaeundaeContractError("repeated platform external res_no")
        native_rows = [_platform_native_row(row) for row in native_source]

        # Exact Haeundae resident-council partition.
        city_first, city_last = fetch_one(
            busan_haeundae_city_list_url(1),
            lambda soup, final: _parse_city_page(soup, final, page=1),
            list_phase=True,
        )
        if city_last > page_cap:
            raise BusanHaeundaeContractError(
                f"max_pages cap allows {page_cap} of {city_last} city pages"
            )
        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first}
        city_pages.update(
            fetch_batch(
                [
                    (
                        page,
                        busan_haeundae_city_list_url(page),
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
            busan_haeundae_city_list_url(city_last + 1),
            lambda soup, final: _parse_city_page(
                soup, final, page=city_last + 1, expected_last=city_last
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if city_empty:
            raise BusanHaeundaeContractError("city sentinel returned rows")
        city_boundaries = sorted({1, city_last})
        city_rechecked = fetch_batch(
            [
                (
                    page,
                    busan_haeundae_city_list_url(page),
                    lambda soup, final, p=page: _parse_city_page(
                        soup, final, page=p, expected_last=city_last
                    )[0],
                )
                for page in city_boundaries
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(city_boundaries)
        for page in city_boundaries:
            if _signature(city_rechecked[page]) != _signature(city_pages[page]):
                raise BusanHaeundaeContractError("city boundary page changed")
        city_rows = [
            row for page in range(1, city_last + 1) for row in city_pages[page]
        ]
        city_ids = [_clean(row.get("provider_course_id")) for row in city_rows]
        if len(city_ids) != len(set(city_ids)):
            raise BusanHaeundaeContractError("duplicate city identity")

        local_current = [
            row
            for row in publishable_local
            if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        native_current = [
            row
            for row in native_rows
            if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        city_current = [
            row for row in city_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        current = local_current + native_current + city_current
        if len(current) > detail_cap:
            raise BusanHaeundaeContractError(
                f"detail_limit cap allows {detail_cap} of {len(current)} current details"
            )
        detail_items: list[
            tuple[str, str, Callable[[BeautifulSoup, str], dict[str, Any]]]
        ] = []
        for row in local_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]),
                    _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_local_detail(
                        soup, final, parent
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
                    lambda soup, final, parent=row: _parse_city_detail(
                        soup, final, parent
                    ),
                )
            )
        enriched_by_id = fetch_batch(detail_items, list_phase=False)
        meta["detail_pages"] = len(detail_items)
        enriched = [
            enriched_by_id[_clean(row["provider_course_id"])] for row in current
        ]
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
            raise BusanHaeundaeContractError("dedupe changed complete identity set")
        if len(after_ids) != len(set(after_ids)):
            raise BusanHaeundaeContractError("duplicate identity remained")

        meta.update(
            {
                "district_source_rows": len(local_rows),
                "district_data_pages": local_last,
                "district_excluded_non_course_rows": len(excluded_rows),
                "district_exclusion_counts": dict(exclusion_counts),
                "district_publishable_rows": len(publishable_local),
                "district_current_count": len(local_current),
                "district_expired_count": len(publishable_local) - len(local_current),
                "platform_source_rows": len(platform_rows),
                "platform_data_pages": platform_last,
                "platform_external_duplicate_rows": len(external_rows),
                "platform_external_unique_resnos": len(set(external_ids)),
                "platform_external_matching_current_district": sum(
                    identity in local_by_id for identity in external_ids
                ),
                "platform_external_audited_tombstones": sum(
                    identity not in local_by_id for identity in external_ids
                ),
                "platform_native_rows": len(native_rows),
                "platform_native_current_count": len(native_current),
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "city_expired_count": len(city_rows) - len(city_current),
                "source_total": len(local_rows) + len(platform_rows) + len(city_rows),
                "duplicate_source_rows": len(external_rows),
                "unique_education_source_rows": len(publishable_local)
                + len(native_rows)
                + len(city_rows),
                "current_source_count": len(current),
                "expired_count": len(publishable_local)
                - len(local_current)
                + len(native_rows)
                - len(native_current)
                + len(city_rows)
                - len(city_current),
                "status_counts": dict(Counter(row.get("status") for row in result)),
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "branch_counts": dict(Counter(row.get("branch") for row in result)),
                "pii_redaction_count": redactions,
                "required_list_requests": meta["list_requests"],
                "required_detail_requests": len(detail_items),
                "network_requests": budget.count,
                "pagination_detected": local_last > 1 or city_last > 1,
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
        return result, BUSAN_HAEUNDAE_PARSER, meta
    except Exception as exc:
        meta["network_requests"] = budget.count
        message = _clean(exc)
        if "cap" in message:
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = message or exc.__class__.__name__
        return [], BUSAN_HAEUNDAE_PARSER, meta


collect_courses = collect_busan_haeundae_education


__all__ = [
    "BUSAN_HAEUNDAE_PROVIDER",
    "BUSAN_HAEUNDAE_CANDIDATE_ID",
    "BUSAN_CITY_HAEUNDAE_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_HAEUNDAE_MUNICIPALITY_CODE",
    "BUSAN_HAEUNDAE_MUNICIPALITY_NAME",
    "BUSAN_HAEUNDAE_REGISTERED_URL",
    "BUSAN_HAEUNDAE_URL",
    "BUSAN_HAEUNDAE_CANONICAL_URL",
    "BUSAN_CITY_HAEUNDAE_URL",
    "BUSAN_LIFELONG_HAEUNDAE_OFFICE",
    "BUSAN_HAEUNDAE_PARSER",
    "BUSAN_HAEUNDAE_CANDIDATE_IDS",
    "BUSAN_HAEUNDAE_OWNER_BOUNDARY_AUDIT",
    "BUSAN_HAEUNDAE_DISCOVERY_AUDIT",
    "BusanHaeundaeContractError",
    "is_busan_haeundae_education_target",
    "is_target",
    "busan_haeundae_list_url",
    "busan_haeundae_detail_url",
    "busan_haeundae_city_list_url",
    "busan_haeundae_city_detail_url",
    "busan_haeundae_lifelong_list_url",
    "busan_haeundae_lifelong_detail_url",
    "collect_busan_haeundae_education",
    "collect_courses",
]
