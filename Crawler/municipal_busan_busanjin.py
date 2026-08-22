"""Atomic education collector for Busan Busanjin-gu's public ledgers.

The configured ``DOM_000000209001004000`` page is an information page, not a
course catalogue.  The district's complete education owner is the unfiltered
``강좌·교육`` ledger behind ``/reserve/index.busanjin``.  The collector keeps
the configured provider identity but always reads that current owner.

Two official companion ledgers are part of the same snapshot.  The Busan
integrated-reservation source is fixed to Busanjin-gu (``srchGugun=7``) and
resident councils (``srchResveInsttCd=33``).  부산평생학습플랫폼 office
``OFFICE_00002710`` republishes district detail URLs.  Its default 100-row
pagination is unstable inside equal sort keys, so this collector requires two
identical complete 1000-row semantic censuses.  External rows are suppressed
only after their exact ``idx`` and immutable list fields match the district
owner; future native ``LEARNING_*`` rows remain independent.

Every declared page, immediate sentinel, stable first/final boundaries and
every current/future detail are mandatory.  A failure in any ledger discards
the union.  Application forms, account/history pages, applicant tables,
attachments, free-form descriptions, instructors and contact values are
never fetched or read.
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
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong


BUSAN_BUSANJIN_PROVIDER = "MUNI_WWW_BUSANJIN_GO_KR_5881F59A"
BUSAN_CITY_BUSANJIN_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_D884D074"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_BUSANJIN_MUNICIPALITY_CODE = "2623000000"
BUSAN_BUSANJIN_MUNICIPALITY_NAME = "부산광역시 부산진구"
BUSAN_BUSANJIN_HOST = "www.busanjin.go.kr"
BUSAN_BUSANJIN_REGISTERED_PATH = "/index.busanjin"
BUSAN_BUSANJIN_REGISTERED_MENU = "DOM_000000209001004000"
BUSAN_BUSANJIN_REGISTERED_URL = (
    f"https://{BUSAN_BUSANJIN_HOST}{BUSAN_BUSANJIN_REGISTERED_PATH}?"
    + urlencode({"menuCd": BUSAN_BUSANJIN_REGISTERED_MENU})
)
BUSAN_BUSANJIN_RESERVE_PATH = "/reserve/index.busanjin"
BUSAN_BUSANJIN_MENU = "DOM_000001501001000000"
BUSAN_BUSANJIN_CANONICAL_URL = (
    f"https://{BUSAN_BUSANJIN_HOST}{BUSAN_BUSANJIN_RESERVE_PATH}?"
    + urlencode({"menuCd": BUSAN_BUSANJIN_MENU})
)
BUSAN_BUSANJIN_URL = BUSAN_BUSANJIN_CANONICAL_URL
BUSAN_BUSANJIN_LIST_PATH = (
    "/user/lifelong/lecture/selectLectureUserSearchEduList.busanjin"
)
BUSAN_BUSANJIN_DETAIL_PATH = "/user/lifelong/lecture/eduDetail.busanjin"
BUSAN_BUSANJIN_APPLY_PATH = "/user/lifelong/lecture/lectureDetailApply.busanjin"
BUSAN_BUSANJIN_PAGE_SIZE = 6

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_BUSANJIN_GUGUN = "7"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_BUSANJIN_CANDIDATE_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    "resveGroupSn=&progrmSn=&srchGugun=7&srchResveInsttCd=33"
)
BUSAN_CITY_BUSANJIN_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_BUSANJIN_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_LIFELONG_BUSANJIN_OFFICE = "OFFICE_00002710"
BUSAN_LIFELONG_BUSANJIN_OFFICE_NAME = "부산진구청"
BUSAN_LIFELONG_BUSANJIN_PAGE_SIZE = 1000
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_OFFICE_URL = _lifelong.BUSAN_LIFELONG_URL

BUSAN_BUSANJIN_FETCH_ATTEMPTS = 3
BUSAN_BUSANJIN_MAX_WORKERS = 12
BUSAN_BUSANJIN_MAX_HTML_BYTES = 12_000_000
BUSAN_BUSANJIN_PARSER = (
    "busan_busanjin_complete_all_education_idx_pages+empty_sentinel+"
    "stable_first_last+busan_reserve_gugun7_office33_complete+empty_sentinel+"
    "lifelong_office00002710_pageunit1000_two_stable_complete_censuses+"
    "external_idx_duplicate_suppression+native_learning_current_details+"
    "all_current_safe_details+identity_bound_apply_no_form_fetch+pii_allowlist+"
    "atomic_three_ledger_snapshot"
)
BUSAN_BUSANJIN_OWNERSHIP_SCOPE = (
    "busanjin_complete_district_education_resident_councils_and_native_"
    "lifelong_platform_courses"
)

BUSAN_BUSANJIN_CANDIDATE_IDS: Mapping[str, str] = {
    "registered_information_page_retargeted_to_complete_owner": (
        "MUNI_IR_7BBE29A9BFD4"
    ),
    "busan_resident_councils": "MUNI_IR_32B236CC359D",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "wrong_municipality_museum_detail": "MUNI_IR_2BA97ED12CEB",
    "wrong_municipality_family_detail": "MUNI_IR_5608F8475923",
}

BUSAN_BUSANJIN_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_BUSANJIN_PROVIDER: {
        "decision": "retain_provider_retarget_information_page_to_complete_owner",
        "candidate_id": BUSAN_BUSANJIN_CANDIDATE_IDS[
            "registered_information_page_retargeted_to_complete_owner"
        ],
        "registered_url": BUSAN_BUSANJIN_REGISTERED_URL,
        "canonical_url": BUSAN_BUSANJIN_CANONICAL_URL,
        "identity_rule": "numeric idx plus immutable lectureCode",
    },
    BUSAN_CITY_BUSANJIN_PROVIDER: {
        "decision": "collect_exact_busanjin_resident_council_partition",
        "candidate_id": BUSAN_BUSANJIN_CANDIDATE_IDS[
            "busan_resident_councils"
        ],
        "candidate_url": BUSAN_CITY_BUSANJIN_CANDIDATE_URL,
        "canonical_url": BUSAN_CITY_BUSANJIN_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_BUSANJIN_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
        "identity_rule": "resveGroupSn plus progrmSn",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_exact_external_idx_duplicates_keep_native_learning_ids",
        "candidate_id": BUSAN_BUSANJIN_CANDIDATE_IDS[
            "busan_lifelong_federation"
        ],
        "url": BUSAN_LIFELONG_OFFICE_URL,
        "office_code": BUSAN_LIFELONG_BUSANJIN_OFFICE,
        "identity_rule": (
            "external busanjin idx belongs to district owner; "
            "LEARNING_* remains independent"
        ),
    },
    "WRONG_MUNICIPALITY_SEARCH_DETAILS": {
        "decision": "exclude",
        "candidate_ids": (
            BUSAN_BUSANJIN_CANDIDATE_IDS[
                "wrong_municipality_museum_detail"
            ],
            BUSAN_BUSANJIN_CANDIDATE_IDS[
                "wrong_municipality_family_detail"
            ],
        ),
        "reason": "single details are owned by other Busan institutions/gu",
    },
    "PRIVATE_AND_NON_EDUCATION_BOUNDARY": {
        "decision": "never_fetch",
        "excluded": (
            "experience, facility, my-reservation, account, application-form, "
            "applicant-list, attachment and free-form routes"
        ),
    },
}

BUSAN_BUSANJIN_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "registered_url": BUSAN_BUSANJIN_REGISTERED_URL,
    "registered_response": "HTTP 200 information page without course rows",
    "canonical_url": BUSAN_BUSANJIN_CANONICAL_URL,
    "district_rows": 977,
    "district_data_pages": 163,
    "district_page_counts": {"1-162": 6, "163": 5},
    "district_sentinel_page": 164,
    "district_status_counts": {"접수마감": 975, "접수중": 2},
    "district_current_rows": 100,
    "district_current_status_counts": {"접수마감": 100},
    "resident_url": BUSAN_CITY_BUSANJIN_URL,
    "resident_rows": 152,
    "resident_data_pages": 16,
    "resident_page_counts": {"1-15": 10, "16": 2},
    "resident_sentinel_page": 17,
    "resident_current_rows": 152,
    "resident_status_counts": {"접수중": 99, "접수마감": 53},
    "resident_branch_count": 20,
    "lifelong_office": BUSAN_LIFELONG_BUSANJIN_OFFICE,
    "lifelong_rows": 977,
    "lifelong_default_data_pages": 10,
    "lifelong_default_pagination": (
        "unstable within equal sort keys; default-page censuses contain duplicates"
    ),
    "lifelong_collector_page_size": BUSAN_LIFELONG_BUSANJIN_PAGE_SIZE,
    "lifelong_data_pages": 1,
    "lifelong_external_rows": 977,
    "lifelong_external_unique_idx": 977,
    "lifelong_native_rows": 0,
    "atomic_current_rows": 252,
    "atomic_required_list_requests": 188,
    "atomic_required_detail_requests": 252,
    "atomic_required_requests_without_retries": 440,
    "conclusion": (
        "collect complete district and resident-council ledgers; suppress every "
        "identity-proved external office republication"
    ),
}

BUSAN_BUSANJIN_PII_FIELDS_NEVER_READ = (
    "local free-form detail, attachment filenames and applicant table values",
    "Busan city 문의 values and detail 문의전화 values",
    "platform instructor, contact, enrolment and free-form values",
    "application forms, account pages and applicant lists",
)


class BusanBusanjinContractError(ValueError):
    """Raised when an audited Busanjin-gu source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport, response, or status-200 error-page failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Probe = Callable[[BeautifulSoup], None]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_LECTURE_CODE_RE = re.compile(r"^20\d{6}[A-Z]{4}\d{4}$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*"
    r"(\d{1,2})(?:\.)?(?!\d)"
)
_LOCAL_TOTAL_RE = re.compile(
    r"총\s*게시물\s*:\s*([\d,]+)\s*건,\s*페이지\s*:\s*"
    r"(\d+)\s*/\s*(\d+)"
)
_LOCAL_ACTION_RE = re.compile(r"^goDetail\(\s*([1-9]\d*)\s*,\s*this\s*\)$")
_LOCAL_PAGE_ACTION_RE = re.compile(
    r"^linkPage\(\s*([1-9]\d*)\s*\);\s*return\s+false;?$"
)
_CITY_ACTION_RE = re.compile(
    r"^fn_viewProgrm\(\s*['\"]([1-9]\d*)['\"]\s*,\s*"
    r"['\"]([1-9]\d*)['\"]\s*\);\s*return\s+false;?$"
)
_CITY_CARD_DATES_RE = re.compile(
    r"^\[신청\]\s*(20\d{2}-\d{2}-\d{2})\s*~\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*\[행사\]\s*"
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_CITY_DETAIL_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LOCAL_STATUS_MAP = {
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_LOCAL_LIST_STATUS_CLASSES = {
    "접수중": "Receipt",
    "접수마감": "Accept",
}
_LOCAL_LIST_REQUIRED_LABELS = (
    "접수기간",
    "강의기간",
    "대상",
    "신청/정원(전체정원)",
    "접수방법",
)
_LOCAL_LIST_OPTIONAL_LABELS = frozenset({"대기신청", "온라인대기"})
_LOCAL_DETAIL_LABELS = (
    "접수기간",
    "대기신청",
    "신청/정원",
    "전체정원",
    "강좌기간",
    "강좌시간",
    "강의실",
    "주최",
    "첨부파일",
)
_LOCAL_DETAIL_SAFE_LABELS = frozenset(_LOCAL_DETAIL_LABELS) - {"첨부파일"}
_LOCAL_DETAIL_STATE_STATUS = {
    "1": "SCHEDULED",
    "2": "OPEN",
    "3": "OPEN",
    "4": "OPEN",
    "5": "CLOSED",
    "6": "CLOSED",
}

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_CITY_DETAIL_REQUIRED_LABELS = (
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
_CITY_DETAIL_SAFE_LABELS = frozenset(_CITY_DETAIL_REQUIRED_LABELS) - {
    "문의전화"
}
_CITY_DETAIL_SKIPPED_LABELS = frozenset({"문의전화", "첨부파일"})

_PLATFORM_DETAIL_REQUIRED_LABELS = (
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
_PLATFORM_DETAIL_OPTIONAL_LABELS = frozenset({"수강료 기타", "직장인 여부"})
_PLATFORM_DETAIL_SAFE_LABELS = frozenset(
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


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanBusanjinContractError(
            f"expected one {label}, found {len(found)}"
        )
    return found[0]


def _query_one(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    return _clean(values[0]) if len(values) == 1 else ""


def _target_value(target: Any, key: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(key)
    return getattr(target, key, None)


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
        raise BusanBusanjinContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanBusanjinContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanBusanjinContractError(f"{label} must be positive")
    return result


def _normal_path(value: str) -> str:
    return re.sub(r"/{2,}", "/", value or "/")


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}"
        + (f"?{query}" if query else "")
    )


def is_busan_busanjin_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_BUSANJIN_PROVIDER:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(BUSAN_BUSANJIN_REGISTERED_URL),
        _compare_url(BUSAN_BUSANJIN_CANONICAL_URL),
    }


is_target = is_busan_busanjin_education_target


def busan_busanjin_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    return f"https://{BUSAN_BUSANJIN_HOST}{BUSAN_BUSANJIN_LIST_PATH}?" + urlencode(
        (
            ("menuCd", BUSAN_BUSANJIN_MENU),
            ("allYn", "Y"),
            ("cpath", "/reserve"),
            ("pageIndex", value),
            ("pageUnit", BUSAN_BUSANJIN_PAGE_SIZE),
            ("pageSize", 5),
            ("orgLectGubun", ""),
        )
    )


def busan_busanjin_detail_url(identity: Any, lecture_code: Any) -> str:
    idx = _clean(identity)
    code = _clean(lecture_code)
    if not _IDENTITY_RE.fullmatch(idx) or not _LECTURE_CODE_RE.fullmatch(code):
        raise BusanBusanjinContractError("invalid Busanjin course identity")
    return f"https://{BUSAN_BUSANJIN_HOST}{BUSAN_BUSANJIN_DETAIL_PATH}?" + urlencode(
        (("menuCd", BUSAN_BUSANJIN_MENU), ("idx", idx), ("lectureCode", code))
    )


def busan_busanjin_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", value),
            ("srchGugun", BUSAN_CITY_BUSANJIN_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_busanjin_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanBusanjinContractError("invalid Busan city course identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def busan_busanjin_lifelong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_BUSANJIN_OFFICE, value)
    payload["pageUnit"] = str(BUSAN_LIFELONG_BUSANJIN_PAGE_SIZE)
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_busanjin_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanBusanjinContractError("invalid lifelong identity")
    return _lifelong.busan_lifelong_detail_url(value)


def canonical_busan_busanjin_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_BUSANJIN_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_BUSANJIN_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd", "idx", "lectureCode"}
        or query.get("menuCd") != [BUSAN_BUSANJIN_MENU]
    ):
        return ""
    identity = _query_one(query, "idx")
    lecture_code = _query_one(query, "lectureCode")
    if not _IDENTITY_RE.fullmatch(identity) or not _LECTURE_CODE_RE.fullmatch(
        lecture_code
    ):
        return ""
    return f"idx:{identity}"


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        }
    )
    return session


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=False)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


class _RequestBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.count = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.count >= self.maximum:
                raise BusanBusanjinContractError(
                    f"max_requests cap {self.maximum} exhausted"
                )
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    if isinstance(response, BeautifulSoup):
        return response, requested_url
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise _TransientFetchError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise _TransientFetchError("redirected source response")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    if (
        final.scheme.lower() != "https"
        or (final.hostname or "").rstrip(".").lower()
        != (requested.hostname or "").rstrip(".").lower()
        or final.port is not None
        or final.username
        or final.password
        or _normal_path(final.path) != _normal_path(requested.path)
        or final.params
        or final.fragment
    ):
        raise _TransientFetchError("source response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise _TransientFetchError("empty source response")
    size = len(content) if isinstance(content, bytes) else len(
        str(content).encode("utf-8")
    )
    if size > BUSAN_BUSANJIN_MAX_HTML_BYTES:
        raise _TransientFetchError("source HTML exceeds safety limit")
    soup = BeautifulSoup(content, "lxml")
    return soup, final_url


@dataclass
class _FetchResult:
    values: dict[Any, tuple[BeautifulSoup, str]]
    errors: list[str]
    retries: int
    sessions: int


def _fetch_many(
    items: Sequence[tuple[Any, str, Probe]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _FetchResult:
    values: dict[Any, tuple[BeautifulSoup, str]] = {}
    errors: list[str] = []
    retries = 0
    sessions: list[Any] = []
    local = threading.local()
    lock = threading.Lock()

    def thread_session() -> Any:
        current = getattr(local, "session", None)
        if current is None:
            current = session_factory()
            local.session = current
            with lock:
                sessions.append(current)
        return current

    def one(item: tuple[Any, str, Probe]) -> tuple[Any, tuple[BeautifulSoup, str], int]:
        key, url, probe = item
        messages: list[str] = []
        for attempt in range(1, BUSAN_BUSANJIN_FETCH_ATTEMPTS + 1):
            try:
                budget.take()
                response = fetcher(thread_session(), url, timeout)
                soup, final_url = _response_soup(response, url)
                probe(soup)
                return key, (soup, final_url), attempt - 1
            except Exception as exc:
                messages.append(
                    f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}"
                )
                if attempt < BUSAN_BUSANJIN_FETCH_ATTEMPTS:
                    sleeper(min(0.05 * attempt, 0.15))
        raise _TransientFetchError("; ".join(messages))

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(one, item): item[0] for item in items}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    found_key, value, item_retries = future.result()
                    values[found_key] = value
                    retries += item_retries
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
    finally:
        for current in sessions:
            _close_quietly(current)
    return _FetchResult(values, errors, retries, len(sessions))


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanBusanjinContractError("invalid source date") from exc
    return result


def _date_range(value: Any, label: str) -> tuple[str, str]:
    found = _dates(value)
    if len(found) != 2 or found[1] < found[0]:
        raise BusanBusanjinContractError(f"{label} changed or is reversed")
    return found[0].isoformat(), found[1].isoformat()


def _direct_field(form: Tag, name: str, *, allow_blank: bool = False) -> str:
    field = _one(form.select(f":scope > input[name='{name}']"), f"{name} field")
    value = _clean(field.get("value"))
    if not value and not allow_blank:
        raise BusanBusanjinContractError(f"empty {name} field")
    return value


def _local_list_contract(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[int, int, Optional[Tag]]:
    roots = soup.select("div.guide-wrap")
    if len(roots) != 1:
        raise BusanBusanjinContractError("expected one district list root")
    root = roots[0]
    form = _one(root.select("form[name='searchForm']"), "district search form")
    action = urlparse(urljoin(busan_busanjin_list_url(page), _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or (action.hostname or "").rstrip(".").lower() != BUSAN_BUSANJIN_HOST
        or _normal_path(action.path) != BUSAN_BUSANJIN_LIST_PATH
    ):
        raise BusanBusanjinContractError("district search form changed")
    expected_fields = {
        "pageIndex": str(page),
        "pageUnit": str(BUSAN_BUSANJIN_PAGE_SIZE),
        "pageSize": "5",
        "menuCd": BUSAN_BUSANJIN_MENU,
        "orgLectGubun": "",
        "allYn": "Y",
    }
    for name, expected in expected_fields.items():
        value = _direct_field(form, name, allow_blank=name == "orgLectGubun")
        if value != expected:
            raise BusanBusanjinContractError(f"district {name} scope changed")
    selected_region = form.select("select[name='eduRegion'] option[selected]")
    if selected_region and _clean(selected_region[0].get("value")):
        raise BusanBusanjinContractError("district list gained a region filter")
    title_field = _one(form.select("input[name='title']"), "district title filter")
    if _clean(title_field.get("value")):
        raise BusanBusanjinContractError("district list gained a title filter")

    total_node = _one(root.select("div.total"), "district total declaration")
    match = _LOCAL_TOTAL_RE.fullmatch(_text(total_node))
    if not match:
        raise BusanBusanjinContractError("district total declaration changed")
    total = int(match.group(1).replace(",", ""))
    displayed_page = int(match.group(2))
    last = int(match.group(3))
    if displayed_page != page or last != max(1, math.ceil(total / BUSAN_BUSANJIN_PAGE_SIZE)):
        raise BusanBusanjinContractError("district page declaration is inconsistent")
    if expected_total is not None and total != expected_total:
        raise BusanBusanjinContractError("district total changed during crawl")
    if expected_last is not None and last != expected_last:
        raise BusanBusanjinContractError("district last page changed during crawl")
    board = root.select("div.board-list > ul.gallery01.li-wt")
    if page <= last:
        list_root: Optional[Tag] = _one(board, "district course list")
    elif page == last + 1:
        if board and board[0].select("a.bd[onclick]"):
            raise BusanBusanjinContractError("district sentinel retained course rows")
        list_root = board[0] if board else None
    else:
        raise BusanBusanjinContractError("district request passed sentinel")
    return total, last, list_root


def _labelled_list_values(card: Tag) -> dict[str, str]:
    result: dict[str, str] = {}
    allowed = set(_LOCAL_LIST_REQUIRED_LABELS) | set(_LOCAL_LIST_OPTIONAL_LABELS)
    for item in card.select(":scope > ul.cont-list > li"):
        text = _text(item)
        if ":" not in text:
            raise BusanBusanjinContractError("district list field lost its label")
        label, value = (_clean(part) for part in text.split(":", 1))
        if not label or label in result or label not in allowed:
            raise BusanBusanjinContractError(
                f"unknown or duplicate district list field {label!r}"
            )
        if not value:
            raise BusanBusanjinContractError(f"empty district list field {label}")
        result[label] = value
    if any(label not in result for label in _LOCAL_LIST_REQUIRED_LABELS):
        raise BusanBusanjinContractError("district list required fields changed")
    return result


def _parse_local_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last, list_root = _local_list_contract(
        soup,
        page=page,
        expected_total=expected_total,
        expected_last=expected_last,
    )
    forms: dict[str, Tag] = {}
    for form in soup.select("form[id^='goEduDetail']"):
        identity = _direct_field(form, "idx")
        if not _IDENTITY_RE.fullmatch(identity) or identity in forms:
            raise BusanBusanjinContractError("district detail-form identity changed")
        forms[identity] = form
    cards = list_root.select(":scope > li > a.bd[onclick]") if list_root else []
    rows: list[dict[str, Any]] = []
    for position, card in enumerate(cards, 1):
        action = _LOCAL_ACTION_RE.fullmatch(_clean(card.get("onclick")))
        if not action:
            raise BusanBusanjinContractError("district card action changed")
        identity = action.group(1)
        form = forms.pop(identity, None)
        if form is None:
            raise BusanBusanjinContractError("district card lacks identity form")
        if _clean(form.get("method")).casefold() != "post" or urlparse(
            _clean(form.get("action"))
        ).path != BUSAN_BUSANJIN_DETAIL_PATH:
            raise BusanBusanjinContractError("district detail form route changed")
        page_value = _direct_field(form, "pageIndex")
        page_unit = _direct_field(form, "pageUnit")
        page_size = _direct_field(form, "pageSize")
        menu = _direct_field(form, "menuCd")
        org = _direct_field(form, "orgLectGubun", allow_blank=True)
        lecture_code = _direct_field(form, "lectureCode")
        lecture_sort = _direct_field(form, "lectureSort")
        period = _direct_field(form, "period")
        period_limit = _direct_field(form, "periodLimit")
        limit_num = _direct_field(form, "limitNum")
        if (
            page_value != str(page)
            or page_unit != str(BUSAN_BUSANJIN_PAGE_SIZE)
            or page_size != "5"
            or menu != BUSAN_BUSANJIN_MENU
            or org
            or not _LECTURE_CODE_RE.fullmatch(lecture_code)
            or not period.isdigit()
            or period_limit not in {"Y", "N"}
            or not limit_num.isdigit()
        ):
            raise BusanBusanjinContractError("district detail form scope changed")
        title = _text(_one(card.select(":scope .course-tit"), "district title"))
        category = _text(
            _one(card.select(":scope .edu-tit"), "district category")
        )
        status_node = _one(
            card.select(":scope i.Accept, :scope i.Receipt"),
            "district status",
        )
        source_status = _text(status_node)
        status_classes = {
            _clean(value) for value in status_node.get("class", ()) if _clean(value)
        }
        if (
            not title
            or not category
            or source_status not in _LOCAL_STATUS_MAP
            or status_classes != {_LOCAL_LIST_STATUS_CLASSES.get(source_status, "")}
        ):
            raise BusanBusanjinContractError("district title/category/status changed")
        safe = _labelled_list_values(card)
        apply_start, apply_end = _date_range(
            safe["접수기간"], "district application period"
        )
        start, end = _date_range(safe["강의기간"], "district education period")
        wait_start = wait_end = ""
        if "대기신청" in safe:
            wait_start, wait_end = _date_range(
                safe["대기신청"], "district waitlist period"
            )
        raw_url = busan_busanjin_detail_url(identity, lecture_code)
        branch = (
            lecture_sort
            if lecture_sort.startswith("부산진구")
            else f"부산진구 {lecture_sort}"
        )
        rows.append(
            {
                "provider": BUSAN_BUSANJIN_PROVIDER,
                "provider_course_id": (
                    f"{BUSAN_BUSANJIN_PROVIDER}:district:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"busanjin-district-{lecture_sort}",
                "preserve_branch": True,
                "category": category,
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": safe["접수방법"],
                "reservation_available": False,
                "status": _LOCAL_STATUS_MAP[source_status],
                "fee": "",
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": "",
                "target": safe["대상"],
                "capacity": safe["신청/정원(전체정원)"],
                "venue_name": lecture_sort,
                "provider_organizer": "부산진구청",
                "municipality_code": BUSAN_BUSANJIN_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_BUSANJIN_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "부산진구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_BUSANJIN_PARSER,
                    "source_catalog": "busanjin_complete_all_education",
                    "source_identity": identity,
                    "source_lecture_code": lecture_code,
                    "source_lecture_sort": lecture_sort,
                    "source_period_id": period,
                    "source_period_limit": period_limit,
                    "source_limit_num": limit_num,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_wait_start": wait_start,
                    "source_wait_end": wait_end,
                    "list_application_control": source_status == "접수중",
                    "detail_verified": False,
                    "attachments_never_read": True,
                    "free_form_detail_never_read": True,
                    "applicant_list_fetched": False,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    if forms:
        raise BusanBusanjinContractError("district orphan detail forms detected")
    expected_count = 0
    if page <= last:
        expected_count = min(
            BUSAN_BUSANJIN_PAGE_SIZE,
            total - BUSAN_BUSANJIN_PAGE_SIZE * (page - 1),
        )
    if len(rows) != expected_count:
        raise BusanBusanjinContractError("district page row count changed")
    return rows, total, last


def _local_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        repr(
            [
                (
                    _clean(row.get("raw_fields", {}).get("source_identity")),
                    _clean(row.get("raw_fields", {}).get("source_lecture_code")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("apply_start")),
                    _clean(row.get("apply_end")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _safe_local_detail_values(root: Tag) -> tuple[dict[str, str], set[str]]:
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    labels: list[str] = []
    values_root = _one(
        root.select(":scope > li > ul.edu-dt-listtype01"),
        "district detail values",
    )
    for item in values_root.find_all("li", recursive=False):
        heading = _one(item.select(":scope > span.tit"), "district detail label")
        label = _text(heading)
        if label in labels or label not in _LOCAL_DETAIL_LABELS:
            raise BusanBusanjinContractError(
                f"unknown or duplicate district detail field {label!r}"
            )
        labels.append(label)
        if label in _LOCAL_DETAIL_SAFE_LABELS:
            clone = BeautifulSoup(str(item), "lxml")
            for node in clone.select("span.tit"):
                node.extract()
            safe[label] = _clean(clone.get_text(" ", strip=True))
        else:
            # The attachment node and filename are deliberately not read.
            skipped.add(label)
    if tuple(labels) != _LOCAL_DETAIL_LABELS or skipped != {"첨부파일"}:
        raise BusanBusanjinContractError("district detail field boundary changed")
    if any(not safe.get(label) for label in _LOCAL_DETAIL_SAFE_LABELS):
        raise BusanBusanjinContractError("district safe detail value is empty")
    return safe, skipped


def _parse_local_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    lecture_code = _clean(raw.get("source_lecture_code"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_BUSANJIN_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_BUSANJIN_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"menuCd", "idx", "lectureCode"}
        or query.get("menuCd") != [BUSAN_BUSANJIN_MENU]
        or query.get("idx") != [identity]
        or query.get("lectureCode") != [lecture_code]
    ):
        raise BusanBusanjinContractError("district detail response scope changed")
    title = _text(_one(soup.select("title"), "district detail document title"))
    if "강좌·교육" not in title or "부산 진구청" not in title:
        raise BusanBusanjinContractError("district detail title changed")
    root = _one(soup.select("ul.edu-dt-listbox"), "district detail root")
    heading = _text(
        _one(root.select(":scope > li > h5.list-tit"), "district detail heading")
    )
    if heading != _clean(parent.get("title")):
        raise BusanBusanjinContractError("district list/detail title mismatch")
    safe, skipped = _safe_local_detail_values(root)
    start, end = _date_range(safe["강좌기간"], "district detail course period")
    apply_start, apply_end = _date_range(
        safe["접수기간"], "district detail application period"
    )
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanBusanjinContractError("district list/detail dates mismatch")

    form = _one(soup.select("form[name='goApplyForm']"), "district apply form")
    if (
        _clean(form.get("method")).casefold() != "post"
        or urlparse(_clean(form.get("action"))).path != BUSAN_BUSANJIN_APPLY_PATH
        or _direct_field(form, "menuCd") != BUSAN_BUSANJIN_MENU
        or _direct_field(form, "idx") != identity
        or _direct_field(form, "lectureCode") != lecture_code
        or _direct_field(form, "lectureName") != _clean(parent.get("title"))
        or _direct_field(form, "lectureSort")
        != _clean(raw.get("source_lecture_sort"))
        or _direct_field(form, "period") != _clean(raw.get("source_period_id"))
    ):
        raise BusanBusanjinContractError("district application identity changed")
    # CSRF and any application payload outside the identity fields above are
    # intentionally not read.  The application form itself is never fetched.
    control = _one(
        root.select(":scope > li > div.btn-wrap"), "district application control"
    )
    state = _clean(control.get("data-state2"))
    test_state = _clean(control.get("data-test2"))
    if state != test_state or state not in _LOCAL_DETAIL_STATE_STATUS:
        raise BusanBusanjinContractError("district detail state changed")
    button = _one(control.select(":scope > a[onclick]"), "district apply button")
    button_label = _text(button)
    if (
        button_label != "신청하기"
        or _clean(button.get("onclick")) != f"goApplyPage({state})"
    ):
        raise BusanBusanjinContractError("district application button changed")
    status_nodes = root.select(":scope > li > p[class^='label-']")
    source_detail_status = _text(_one(status_nodes, "district detail status"))
    normalized_status = _LOCAL_DETAIL_STATE_STATUS[state]
    expected_status_text = {
        "SCHEDULED": {"접수대기", "접수예정"},
        "OPEN": {"접수중", "대기신청"},
        "CLOSED": {"접수마감"},
    }[normalized_status]
    if source_detail_status not in expected_status_text:
        raise BusanBusanjinContractError("district detail status/state mismatch")
    list_status = _clean(raw.get("source_status"))
    if _LOCAL_STATUS_MAP.get(list_status) != normalized_status:
        raise BusanBusanjinContractError("district list/detail status mismatch")

    active = normalized_status == "OPEN"
    result = dict(parent)
    result.update(
        {
            "status": normalized_status,
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY"
                if active and state == "4"
                else "ONLINE_RESERVATION"
                if active
                else "INFO_ONLY"
            ),
            "reservation_available": active,
            "schedule_raw": safe["강좌시간"],
            "venue_name": safe["강의실"],
            "provider_organizer": safe["주최"],
            "capacity": safe["신청/정원"],
            "capacity_total_raw": safe["전체정원"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": source_detail_status,
        "detail_state": state,
        "detail_application_control": active,
        "detail_application_control_label": button_label,
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": len(
            soup.select("div.dt-conbox-infor")
        )
        == 1,
        "applicant_table_values_never_read": len(
            soup.select("div.dt-conbox-list")
        )
        == 1,
        "applicant_list_fetched": False,
        "application_form_fetched": False,
    }
    if not (
        result["raw_fields"]["free_form_detail_never_read"]
        and result["raw_fields"]["applicant_table_values_never_read"]
    ):
        raise BusanBusanjinContractError("district private detail boundary changed")
    return result


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_BUSANJIN_OFFICE
    )
    if (
        office is None
        or office.name != BUSAN_LIFELONG_BUSANJIN_OFFICE_NAME
        or office.ownership != "duplicate_dedicated_busanjin_owner"
    ):
        raise BusanBusanjinContractError(
            "lifelong Busanjin-gu office ownership changed"
        )
    return office


def _parse_platform_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    office = _platform_office()
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    errors.extend(_lifelong._form_errors(soup, office, page))
    last, last_errors = _lifelong._advertised_last(soup)
    errors.extend(last_errors)
    if errors:
        raise BusanBusanjinContractError("; ".join(errors))
    if expected_last is not None and last != expected_last:
        raise BusanBusanjinContractError("lifelong displayed last page changed")
    if last != 1:
        raise BusanBusanjinContractError(
            "lifelong pageUnit=1000 no longer yields one data page"
        )
    if page == 1:
        sequences = sorted(
            int(row.get("raw_fields", {}).get("list_sequence") or 0)
            for row in rows
        )
        if sequences != list(range(1, len(rows) + 1)):
            raise BusanBusanjinContractError("lifelong complete sequence has a gap")
    elif page == 2:
        if rows:
            raise BusanBusanjinContractError("lifelong sentinel is not empty")
    else:
        raise BusanBusanjinContractError("lifelong request passed sentinel")
    return rows, last


def _platform_archive_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("raw_fields", {}).get("identity_kind")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    raw = dict(result.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if not _LEARNING_ID_RE.fullmatch(identity):
        raise BusanBusanjinContractError("invalid native lifelong identity")
    result.update(
        {
            "provider": BUSAN_BUSANJIN_PROVIDER,
            "provider_course_id": (
                f"{BUSAN_BUSANJIN_PROVIDER}:lifelong:{identity}"
            ),
            "prefer_incoming_provider_course_id": True,
            "municipality_code": BUSAN_BUSANJIN_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_BUSANJIN_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "부산진구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "complete_shared_office+native_current_detail",
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_BUSANJIN_PARSER,
        "source_catalog": "busan_lifelong_busanjin_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "contact_value_never_read": True,
        "instructor_value_never_read": True,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "applicant_list_fetched": False,
        "application_form_fetched": False,
        "service_family": "education",
    }
    return result


def _safe_platform_detail_values(
    soup: BeautifulSoup,
) -> tuple[tuple[str, ...], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    allowed = set(_PLATFORM_DETAIL_REQUIRED_LABELS) | set(
        _PLATFORM_DETAIL_OPTIONAL_LABELS
    )
    for definition in soup.select("div.form_group dl"):
        heading = _one(
            definition.find_all("dt", recursive=False), "lifelong detail label"
        )
        value = _one(
            definition.find_all("dd", recursive=False), "lifelong detail value"
        )
        label = _text(heading)
        if not label or label in labels or label not in allowed:
            raise BusanBusanjinContractError(
                f"unknown or duplicate lifelong detail field {label!r}"
            )
        labels.append(label)
        if label in _PLATFORM_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            # Unsafe values are deliberately not converted to text.
            skipped.add(label)
    without_optional = [
        label for label in labels if label not in _PLATFORM_DETAIL_OPTIONAL_LABELS
    ]
    if tuple(without_optional) != _PLATFORM_DETAIL_REQUIRED_LABELS:
        raise BusanBusanjinContractError("lifelong detail field order changed")
    expected_skipped = (
        set(_PLATFORM_DETAIL_REQUIRED_LABELS)
        | (set(labels) & set(_PLATFORM_DETAIL_OPTIONAL_LABELS))
    ) - set(_PLATFORM_DETAIL_SAFE_LABELS)
    if skipped != expected_skipped:
        raise BusanBusanjinContractError("lifelong private field boundary changed")
    return tuple(labels), safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower()
        != _lifelong.BUSAN_LIFELONG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_LIFELONG_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"lng_id"}
        or query.get("lng_id") != [identity]
    ):
        raise BusanBusanjinContractError("lifelong detail response scope changed")
    form = _one(
        soup.select("form#learningVO[name='learningVO']"), "lifelong detail form"
    )
    action = urlparse(urljoin(final_url, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != BUSAN_LIFELONG_DETAIL_PATH
        or parse_qs(action.query, keep_blank_values=True).get("lng_id") != [identity]
    ):
        raise BusanBusanjinContractError("lifelong detail form changed")
    identity_fields = {
        _clean(node.get("value")) for node in form.select("input[name='lng_id']")
    }
    office_fields = {
        _clean(node.get("value")) for node in form.select("input[name='inst_id']")
    }
    if identity_fields != {identity} or office_fields != {
        BUSAN_LIFELONG_BUSANJIN_OFFICE
    }:
        raise BusanBusanjinContractError("lifelong identity/office mismatch")
    heading = _one(soup.select("h2.enrolTit"), "lifelong detail heading")
    prefix = _one(heading.select(":scope > span"), "lifelong office prefix")
    if _text(prefix) != f"[{BUSAN_LIFELONG_BUSANJIN_OFFICE_NAME}]":
        raise BusanBusanjinContractError("lifelong office prefix changed")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")):
        raise BusanBusanjinContractError("lifelong list/detail title mismatch")
    labels, safe, _skipped = _safe_platform_detail_values(soup)
    detail_start, detail_end = _date_range(
        safe.get("교육기간"), "lifelong detail education period"
    )
    if (detail_start, detail_end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanBusanjinContractError("lifelong detail education dates mismatch")
    if parent.get("apply_start") and parent.get("apply_end"):
        detail_apply_start, detail_apply_end = _date_range(
            safe.get("일반모집기간"), "lifelong detail application period"
        )
        if (detail_apply_start, detail_apply_end) != (
            _clean(parent.get("apply_start")),
            _clean(parent.get("apply_end")),
        ):
            raise BusanBusanjinContractError(
                "lifelong detail application dates mismatch"
            )
    controls = soup.select("#learning_aply_btn")
    source_status = _clean(raw.get("source_status"))
    active = source_status in {"접수중", "대기접수"}
    control_label = ""
    application_type = "INFO_ONLY"
    if active:
        control = _one(controls, "lifelong application control")
        control_label = _text(control)
        if (
            control_label
            not in {"우선모집신청", "일반모집신청", "수강신청", "대기자신청"}
            or _clean(control.get("onclick")) != "fn_learning_apply(); return false;"
        ):
            raise BusanBusanjinContractError(
                "lifelong application control changed"
            )
        application_type = (
            "WAITLIST_APPLY"
            if control_label == "대기자신청"
            else "ONLINE_RESERVATION"
        )
    elif controls:
        raise BusanBusanjinContractError("closed lifelong row became actionable")
    result = dict(parent)
    result.update(
        {
            "status": "OPEN" if active else "CLOSED",
            "application_url": (
                busan_busanjin_lifelong_detail_url(identity) if active else ""
            ),
            "application_type": application_type,
            "reservation_available": active,
            "target": safe.get("교육대상", ""),
            "venue_name": (
                safe.get("교육장소") or BUSAN_LIFELONG_BUSANJIN_OFFICE_NAME
            ),
            "fee": safe.get("수강료", ""),
            "schedule_raw": safe.get("교육시간", ""),
            "application_method_raw": safe.get("모집방법", ""),
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": active,
        "detail_application_control_label": control_label,
        "detail_source_status": safe.get("신청상태", ""),
        "contact_value_never_read": True,
        "instructor_value_never_read": True,
        "enrollment_counts_never_read": True,
        "optional_free_form_values_never_read": True,
        "workplace_eligibility_value_never_read": "직장인 여부" in labels,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, *, page: int, expected_last: Optional[int] = None
) -> tuple[int, Optional[Tag]]:
    if _text(_one(soup.select("title"), "Busan city title")) != _CITY_LIST_TITLE:
        raise BusanBusanjinContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
        or _clean(_one(form.select("input[name='curPage']"), "city page field").get("value"))
        != str(page)
    ):
        raise BusanBusanjinContractError("Busan city search form changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_BUSANJIN_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanBusanjinContractError(f"Busan city {name} filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "city last page")
    parsed = urlparse(urljoin(BUSAN_CITY_BUSANJIN_URL, _clean(end_link.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_CITY_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or query.get("srchGugun") != [BUSAN_CITY_BUSANJIN_GUGUN]
        or query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
    ):
        raise BusanBusanjinContractError("unsafe Busan city last-page control")
    last_raw = _query_one(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanBusanjinContractError("invalid Busan city last page")
    last = int(last_raw)
    if expected_last is not None and last != expected_last:
        raise BusanBusanjinContractError("Busan city last page changed")
    roots = soup.select("ul.reserveList")
    if page <= last:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last + 1:
        if roots:
            raise BusanBusanjinContractError("Busan city sentinel retained list")
        root = None
    else:
        raise BusanBusanjinContractError("Busan city request passed sentinel")
    return last, root


def _city_card_date_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanBusanjinContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (
            date.fromisoformat(part) for part in match.groups()
        )
    except ValueError as exc:
        raise BusanBusanjinContractError(f"{label} contains invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanBusanjinContractError(f"{label} is reversed")
    return (
        apply_start.isoformat(),
        apply_end.isoformat(),
        start.isoformat(),
        end.isoformat(),
    )


def _parse_city_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(
        soup, page=page, expected_last=expected_last
    )
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"), "Busan city course"
        )
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanBusanjinContractError("Busan city identity action changed")
        group_id, program_id = action.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        normalized_title_attribute = title_attribute
        if title.startswith("[권역]") and title_attribute == title.removeprefix("[권역]"):
            normalized_title_attribute = title
        if not title or normalized_title_attribute != title:
            raise BusanBusanjinContractError("Busan city card title changed")
        source_status = _text(
            _one(link.select(":scope .statusMark"), "Busan city status")
        )
        if source_status not in _CITY_STATUS_MAP:
            raise BusanBusanjinContractError("unknown Busan city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanBusanjinContractError("Busan city card labels changed")
        # 문의 is intentionally the final pair and its value is never read.
        safe = {
            label: _text(value)
            for label, value in zip(labels[:-1], values[:-1])
        }
        if any(not value for value in safe.values()):
            raise BusanBusanjinContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if not branch.startswith("부산진구 ") or not branch.endswith(" 주민자치회"):
            raise BusanBusanjinContractError("Busan city row left Busanjin owner")
        apply_start, apply_end, start, end = _city_card_date_ranges(
            safe["일자"], f"Busan city page {page} row {position} dates"
        )
        raw_url = busan_busanjin_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_BUSANJIN_PROVIDER,
                "provider_course_id": (
                    f"{BUSAN_BUSANJIN_PROVIDER}:reserve:{group_id}:{program_id}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"busanjin-reserve-{group_id}",
                "preserve_branch": True,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": safe["방법"],
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
                "municipality_code": BUSAN_BUSANJIN_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_BUSANJIN_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "부산진구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_BUSANJIN_PARSER,
                    "source_catalog": "busan_reserve_busanjin_resident_councils",
                    "source_identity": f"{group_id}:{program_id}",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": safe["방법"],
                    "source_card_dates": safe["일자"],
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    expected_count = 0 if page == last + 1 else 10 if page < last else len(rows)
    if page < last and len(rows) != expected_count:
        raise BusanBusanjinContractError("Busan city intermediate page is short")
    if page == last and not 1 <= len(rows) <= 10:
        raise BusanBusanjinContractError("Busan city final page row count changed")
    return rows, last


def _city_signature(rows: Sequence[Mapping[str, Any]]) -> str:
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


def _city_detail_dates(value: Any, label: str) -> tuple[str, str]:
    found = _CITY_DETAIL_DATE_RE.findall(_clean(value))
    if len(found) != 2:
        raise BusanBusanjinContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(part) for part in found)
    except ValueError as exc:
        raise BusanBusanjinContractError(f"{label} has invalid date") from exc
    if end < start:
        raise BusanBusanjinContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _city_method_key(value: Any) -> str:
    # The list template emits an empty method slot as `, ,` when one of the
    # three method flags is absent; the detail template omits that slot.
    text = re.sub(r",\s*,", ",", _clean(value))
    return "".join(text.split())


def _safe_city_detail_values(info: Tag) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(
            definition.find_all("dt", recursive=False), "Busan city detail label"
        )
        value = _one(
            definition.find_all("dd", recursive=False), "Busan city detail value"
        )
        label = _text(heading)
        if label in labels:
            raise BusanBusanjinContractError("duplicate Busan city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _CITY_DETAIL_SKIPPED_LABELS:
            # Inquiry/attachment values are deliberately not read.
            skipped.add(label)
        else:
            raise BusanBusanjinContractError(
                f"unknown Busan city detail field {label!r}"
            )
    without_attachment = [label for label in labels if label != "첨부파일"]
    if tuple(without_attachment) != _CITY_DETAIL_REQUIRED_LABELS:
        raise BusanBusanjinContractError("Busan city detail field order changed")
    if "문의전화" not in skipped:
        raise BusanBusanjinContractError("Busan city inquiry boundary changed")
    return labels, safe, skipped


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_CITY_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"resveGroupSn", "progrmSn"}
        or query.get("resveGroupSn") != [group_id]
        or query.get("progrmSn") != [program_id]
    ):
        raise BusanBusanjinContractError("Busan city detail response scope changed")
    if _text(_one(soup.select("title"), "Busan city detail title")) != _CITY_LIST_TITLE:
        raise BusanBusanjinContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanBusanjinContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanBusanjinContractError("Busan city detail identity changed")
    heading = _one(
        form.select(":scope > div.contHeader > h3.titPage"), "city detail heading"
    )
    source_status = _text(
        _one(heading.select(":scope .statusMark"), "city detail status")
    )
    direct_title = _clean(
        " ".join(
            _clean(child)
            for child in heading.children
            if isinstance(child, NavigableString) and _clean(child)
        )
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanBusanjinContractError("Busan city list/detail title mismatch")
    if source_status != _clean(raw.get("source_status")):
        raise BusanBusanjinContractError("Busan city list/detail status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    labels, safe, skipped = _safe_city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanBusanjinContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanBusanjinContractError("Busan city free-form boundary changed")
    start, end = _city_detail_dates(safe["운영기간"], "city operating period")
    apply_start, apply_end = _city_detail_dates(
        safe["신청기간"], "city application period"
    )
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanBusanjinContractError("Busan city list/detail dates mismatch")
    for label, expected in (
        ("신청방법", raw.get("source_application_method")),
        ("운영기관", parent.get("branch")),
        ("대상", parent.get("target")),
    ):
        actual_key = (
            _city_method_key(safe[label])
            if label == "신청방법"
            else _clean(safe[label])
        )
        expected_key = (
            _city_method_key(expected)
            if label == "신청방법"
            else _clean(expected)
        )
        if actual_key != expected_key:
            raise BusanBusanjinContractError(
                f"Busan city list/detail {label} mismatch"
            )
    controls = form.select(
        ":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL"
    )
    if len(controls) > 1:
        raise BusanBusanjinContractError("multiple Busan city application controls")
    control_label = _text(controls[0]) if controls else ""
    normalized_status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if not controls or not any(
                token in control_label for token in ("신청", "예약")
            ):
                raise BusanBusanjinContractError(
                    "open online city row lacks identity-bound control"
                )
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("방문", "전화")):
            if control_label not in {"", "방문예약", "전화접수"}:
                raise BusanBusanjinContractError(
                    "offline Busan city control changed"
                )
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanBusanjinContractError("unknown Busan city application method")
    elif normalized_status == "CLOSED":
        if control_label not in {"", "접수마감"}:
            raise BusanBusanjinContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED":
        if control_label not in {"", "대기중", "접수대기"}:
            raise BusanBusanjinContractError("scheduled Busan city control changed")
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
    }
    return result


_PII_KEY_PARTS = (
    "phone",
    "telephone",
    "telno",
    "mobile",
    "email",
    "instructor",
    "teacher",
    "강사",
    "전화",
    "메일",
    "applicant",
)


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    return any(part in lowered for part in _PII_KEY_PARTS)


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def visit(value: Any) -> Any:
        nonlocal redactions
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, child in value.items():
                if _pii_key(key):
                    redactions += 1
                    continue
                result[str(key)] = visit(child)
            return result
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, str):
            updated, phones = _PHONE_RE.subn("", value)
            updated, emails = _EMAIL_RE.subn("", updated)
            redactions += phones + emails
            return _clean(updated)
        return value

    return visit(row), redactions


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "source_total": 0,
        "source_rows": 0,
        "unique_education_source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "non_current_count": 0,
        "returned_count": 0,
        "district_source_rows": 0,
        "district_data_pages": 0,
        "district_current_count": 0,
        "platform_source_rows": 0,
        "platform_native_rows": 0,
        "platform_native_current_count": 0,
        "platform_external_duplicate_rows": 0,
        "platform_external_unmatched_rows": 0,
        "city_source_rows": 0,
        "city_data_pages": 0,
        "city_current_count": 0,
        "application_control_count": 0,
        "offline_application_count": 0,
        "status_counts": {},
        "branch_counts": {},
        "branch_count": 0,
        "privacy_redactions": 0,
        "duplicate_source_identity_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "atomic_union_complete": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
        "parser": BUSAN_BUSANJIN_PARSER,
        "provider": BUSAN_BUSANJIN_PROVIDER,
        "municipality_code": BUSAN_BUSANJIN_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_BUSANJIN_MUNICIPALITY_NAME,
        "registered_url": BUSAN_BUSANJIN_REGISTERED_URL,
        "canonical_url": BUSAN_BUSANJIN_CANONICAL_URL,
        "city_canonical_url": BUSAN_CITY_BUSANJIN_URL,
        "lifelong_office_code": BUSAN_LIFELONG_BUSANJIN_OFFICE,
        "ownership_scope": BUSAN_BUSANJIN_OWNERSHIP_SCOPE,
        "candidate_ids": dict(BUSAN_BUSANJIN_CANDIDATE_IDS),
        "owner_boundary_audit": dict(BUSAN_BUSANJIN_OWNER_BOUNDARY_AUDIT),
        "discovery_audit": dict(BUSAN_BUSANJIN_DISCOVERY_AUDIT),
        "pii_fields_never_read": BUSAN_BUSANJIN_PII_FIELDS_NEVER_READ,
    }


def _unique_rows(
    rows: Sequence[Mapping[str, Any]], *, key_path: str, label: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if key_path == "source_identity":
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        elif key_path == "platform_identity":
            identity = _clean(row.get("raw_fields", {}).get("identity"))
        else:
            identity = _clean(row.get("provider_course_id"))
        if not identity or identity in result:
            raise BusanBusanjinContractError(f"{label} has duplicate identity")
        result[identity] = dict(row)
    return result


def _same_owner_fields(
    external: Mapping[str, Any], owner: Mapping[str, Any]
) -> bool:
    return all(
        _clean(external.get(key)) == _clean(owner.get(key))
        for key in ("title", "start_date", "end_date", "apply_start", "apply_end")
    )


def collect_busan_busanjin_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 250,
    detail_limit: int = 300,
    max_requests: int = 600,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_BUSANJIN_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of every Busanjin ledger."""

    meta = _base_meta()
    if not is_busan_busanjin_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact registered/canonical Busanjin "
            "education owner"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta
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
        workers = min(max(1, int(max_workers)), BUSAN_BUSANJIN_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_BUSANJIN_PARSER, meta
    if page_cap < 3 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "caps do not allow the three mandatory first-ledger requests"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def run_jobs(
        jobs: Sequence[tuple[Any, str, Probe]], *, list_phase: bool
    ) -> _FetchResult:
        result = _fetch_many(
            jobs,
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            max_workers=min(workers, max(1, len(jobs))),
            sleeper=sleeper,
            budget=budget,
        )
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += len(result.values)
            meta["pages"] += len(result.values)
        return result

    first_jobs: list[tuple[Any, str, Probe]] = [
        (
            ("local", 1),
            busan_busanjin_list_url(1),
            lambda soup: _parse_local_page(soup, page=1),
        ),
        (
            ("platform", "first"),
            busan_busanjin_lifelong_list_url(1),
            lambda soup: _parse_platform_page(soup, page=1),
        ),
        (
            ("city", 1),
            busan_busanjin_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1),
        ),
    ]
    first = run_jobs(first_jobs, list_phase=True)
    if first.errors or len(first.values) != len(first_jobs):
        meta["configured_collection_error"] = "; ".join(first.errors) or (
            "missing one or more first-ledger responses"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta

    try:
        first_local, local_total, local_last = _parse_local_page(
            first.values[("local", 1)][0], page=1
        )
        first_platform, platform_last = _parse_platform_page(
            first.values[("platform", "first")][0], page=1
        )
        first_city, city_last = _parse_city_page(
            first.values[("city", 1)][0], page=1
        )
        required_list_floor = (local_last + 1) + 3 + (city_last + 1) + 4
        if required_list_floor > page_cap:
            raise BusanBusanjinContractError(
                f"max_pages cap allows {page_cap} of {required_list_floor} "
                "required list/sentinel/recheck pages"
            )
        if required_list_floor > request_cap:
            raise BusanBusanjinContractError(
                f"max_requests cap {request_cap} is below list census floor "
                f"{required_list_floor}"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"first-page contract: {_clean(exc)}"
        return [], BUSAN_BUSANJIN_PARSER, meta

    remaining_jobs: list[tuple[Any, str, Probe]] = []
    for page in range(2, local_last + 2):
        remaining_jobs.append(
            (
                ("local", page),
                busan_busanjin_list_url(page),
                lambda soup, page=page: _parse_local_page(
                    soup,
                    page=page,
                    expected_total=local_total,
                    expected_last=local_last,
                ),
            )
        )
    remaining_jobs.extend(
        (
            (
                ("platform", "second"),
                busan_busanjin_lifelong_list_url(1),
                lambda soup: _parse_platform_page(
                    soup, page=1, expected_last=platform_last
                ),
            ),
            (
                ("platform", "sentinel"),
                busan_busanjin_lifelong_list_url(2),
                lambda soup: _parse_platform_page(
                    soup, page=2, expected_last=platform_last
                ),
            ),
        )
    )
    for page in range(2, city_last + 2):
        remaining_jobs.append(
            (
                ("city", page),
                busan_busanjin_city_list_url(page),
                lambda soup, page=page: _parse_city_page(
                    soup, page=page, expected_last=city_last
                ),
            )
        )
    remaining = run_jobs(remaining_jobs, list_phase=True)
    if remaining.errors or len(remaining.values) != len(remaining_jobs):
        meta["configured_collection_error"] = "; ".join(remaining.errors) or (
            "missing complete ledger/sentinel response"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta

    try:
        local_pages: dict[int, list[dict[str, Any]]] = {1: first_local}
        for page in range(2, local_last + 2):
            rows, _, _ = _parse_local_page(
                remaining.values[("local", page)][0],
                page=page,
                expected_total=local_total,
                expected_last=local_last,
            )
            local_pages[page] = rows
        if local_pages[local_last + 1]:
            raise BusanBusanjinContractError("district sentinel is not empty")
        local_rows = [
            row for page in range(1, local_last + 1) for row in local_pages[page]
        ]
        if len(local_rows) != local_total:
            raise BusanBusanjinContractError("district rows differ from total")
        local_by_id = _unique_rows(
            local_rows, key_path="source_identity", label="district census"
        )

        second_platform, _ = _parse_platform_page(
            remaining.values[("platform", "second")][0],
            page=1,
            expected_last=platform_last,
        )
        sentinel_platform, _ = _parse_platform_page(
            remaining.values[("platform", "sentinel")][0],
            page=2,
            expected_last=platform_last,
        )
        if sentinel_platform:
            raise BusanBusanjinContractError("lifelong sentinel is not empty")
        if _platform_archive_signature(first_platform) != _platform_archive_signature(
            second_platform
        ):
            raise BusanBusanjinContractError(
                "lifelong complete semantic census changed"
            )
        _unique_rows(
            first_platform,
            key_path="platform_identity",
            label="lifelong complete census",
        )

        city_pages: dict[int, list[dict[str, Any]]] = {1: first_city}
        for page in range(2, city_last + 2):
            rows, _ = _parse_city_page(
                remaining.values[("city", page)][0],
                page=page,
                expected_last=city_last,
            )
            city_pages[page] = rows
        if city_pages[city_last + 1]:
            raise BusanBusanjinContractError("Busan city sentinel is not empty")
        city_rows = [
            row for page in range(1, city_last + 1) for row in city_pages[page]
        ]
        _unique_rows(city_rows, key_path="source_identity", label="city census")
    except Exception as exc:
        meta["configured_collection_error"] = f"complete census: {_clean(exc)}"
        return [], BUSAN_BUSANJIN_PARSER, meta

    try:
        external_rows: list[dict[str, Any]] = []
        external_by_owner_id: dict[str, dict[str, Any]] = {}
        native_rows: list[dict[str, Any]] = []
        for row in first_platform:
            raw = row.get("raw_fields", {})
            kind = _clean(raw.get("identity_kind"))
            if kind == "external":
                owner_key = canonical_busan_busanjin_course_identity(
                    raw.get("identity")
                )
                identity = owner_key.removeprefix("idx:") if owner_key else ""
                owner = local_by_id.get(identity)
                if (
                    owner is None
                    or not _same_owner_fields(row, owner)
                    or _compare_url(raw.get("identity"))
                    != _compare_url(owner.get("raw_url"))
                ):
                    raise BusanBusanjinContractError(
                        "lifelong external row is not an exact district duplicate"
                    )
                external = dict(row)
                source_fee = _clean(external.get("fee"))
                if not source_fee or identity in external_by_owner_id:
                    raise BusanBusanjinContractError(
                        "lifelong external fee provenance is incomplete or duplicated"
                    )
                external_rows.append(external)
                external_by_owner_id[identity] = external
            elif kind == "internal":
                native_rows.append(_platform_native_row(row))
            else:
                raise BusanBusanjinContractError(
                    f"unsupported lifelong identity kind {kind!r}"
                )
        if set(external_by_owner_id) != set(local_by_id):
            raise BusanBusanjinContractError(
                "lifelong external fee provenance does not cover the district owner"
            )
        cutoff_iso = cutoff.isoformat()
        local_current: list[dict[str, Any]] = []
        for row in local_rows:
            if _clean(row.get("end_date")) < cutoff_iso:
                continue
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            external = external_by_owner_id[identity]
            enriched_owner = dict(row)
            source_fee = _clean(external.get("fee"))
            enriched_owner["fee"] = source_fee
            enriched_owner["raw_fields"] = {
                **dict(row.get("raw_fields", {})),
                "source_fee": source_fee,
                "source_fee_label": "재료비",
                "fee_evidence": (
                    "official_lifelong_exact_owner_duplicate_list"
                ),
            }
            local_current.append(enriched_owner)
        native_current = [
            row for row in native_rows if _clean(row.get("end_date")) >= cutoff_iso
        ]
        city_current = [
            row for row in city_rows if _clean(row.get("end_date")) >= cutoff_iso
        ]
        current_rows = [*local_current, *native_current, *city_current]
        if len(current_rows) > detail_cap:
            raise BusanBusanjinContractError(
                f"detail_limit cap allows {detail_cap} of "
                f"{len(current_rows)} current details"
            )
        required_total_requests = meta["list_requests"] + len(current_rows) + 4
        if required_total_requests > request_cap:
            raise BusanBusanjinContractError(
                f"max_requests cap {request_cap} cannot finish "
                f"{required_total_requests} required requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = (
            f"ownership/current partition: {_clean(exc)}"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta

    detail_jobs: list[tuple[Any, str, Probe]] = []
    for row in local_current:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        lecture_code = _clean(
            row.get("raw_fields", {}).get("source_lecture_code")
        )
        url = busan_busanjin_detail_url(identity, lecture_code)
        detail_jobs.append(
            (
                ("detail", "local", identity),
                url,
                lambda soup, row=row, url=url: _parse_local_detail(soup, url, row),
            )
        )
    for row in native_current:
        identity = _clean(row.get("raw_fields", {}).get("identity"))
        url = busan_busanjin_lifelong_detail_url(identity)
        detail_jobs.append(
            (
                ("detail", "platform", identity),
                url,
                lambda soup, row=row, url=url: _parse_platform_detail(soup, url, row),
            )
        )
    for row in city_current:
        raw = row.get("raw_fields", {})
        group_id = _clean(raw.get("source_group_id"))
        program_id = _clean(raw.get("source_program_id"))
        url = busan_busanjin_city_detail_url(group_id, program_id)
        detail_jobs.append(
            (
                ("detail", "city", group_id, program_id),
                url,
                lambda soup, row=row, url=url: _parse_city_detail(soup, url, row),
            )
        )
    details = run_jobs(detail_jobs, list_phase=False)
    meta["detail_attempts"] = len(detail_jobs)
    meta["detail_errors"] = len(details.errors)
    if details.errors or len(details.values) != len(detail_jobs):
        meta["configured_collection_error"] = "; ".join(details.errors) or (
            "missing one or more current/future details"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta
    try:
        enriched: list[dict[str, Any]] = []
        for row in local_current:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            soup, final_url = details.values[("detail", "local", identity)]
            enriched.append(_parse_local_detail(soup, final_url, row))
        for row in native_current:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            soup, final_url = details.values[("detail", "platform", identity)]
            enriched.append(_parse_platform_detail(soup, final_url, row))
        for row in city_current:
            raw = row.get("raw_fields", {})
            group_id = _clean(raw.get("source_group_id"))
            program_id = _clean(raw.get("source_program_id"))
            soup, final_url = details.values[
                ("detail", "city", group_id, program_id)
            ]
            enriched.append(_parse_city_detail(soup, final_url, row))
    except Exception as exc:
        meta["detail_errors"] += 1
        meta["configured_collection_error"] = f"detail contract: {_clean(exc)}"
        return [], BUSAN_BUSANJIN_PARSER, meta

    recheck_jobs: list[tuple[Any, str, Probe]] = [
        (
            ("recheck", "local", "first"),
            busan_busanjin_list_url(1),
            lambda soup: _parse_local_page(
                soup,
                page=1,
                expected_total=local_total,
                expected_last=local_last,
            ),
        ),
        (
            ("recheck", "local", "last"),
            busan_busanjin_list_url(local_last),
            lambda soup: _parse_local_page(
                soup,
                page=local_last,
                expected_total=local_total,
                expected_last=local_last,
            ),
        ),
        (
            ("recheck", "city", "first"),
            busan_busanjin_city_list_url(1),
            lambda soup: _parse_city_page(
                soup, page=1, expected_last=city_last
            ),
        ),
        (
            ("recheck", "city", "last"),
            busan_busanjin_city_list_url(city_last),
            lambda soup: _parse_city_page(
                soup, page=city_last, expected_last=city_last
            ),
        ),
    ]
    rechecks = run_jobs(recheck_jobs, list_phase=True)
    meta["stability_rechecks"] = len(rechecks.values)
    if rechecks.errors or len(rechecks.values) != len(recheck_jobs):
        meta["configured_collection_error"] = "; ".join(rechecks.errors) or (
            "missing boundary stability rechecks"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta
    try:
        local_first_check, _, _ = _parse_local_page(
            rechecks.values[("recheck", "local", "first")][0],
            page=1,
            expected_total=local_total,
            expected_last=local_last,
        )
        local_last_check, _, _ = _parse_local_page(
            rechecks.values[("recheck", "local", "last")][0],
            page=local_last,
            expected_total=local_total,
            expected_last=local_last,
        )
        city_first_check, _ = _parse_city_page(
            rechecks.values[("recheck", "city", "first")][0],
            page=1,
            expected_last=city_last,
        )
        city_last_check, _ = _parse_city_page(
            rechecks.values[("recheck", "city", "last")][0],
            page=city_last,
            expected_last=city_last,
        )
        if (
            _local_signature(local_first_check) != _local_signature(local_pages[1])
            or _local_signature(local_last_check)
            != _local_signature(local_pages[local_last])
            or _city_signature(city_first_check) != _city_signature(city_pages[1])
            or _city_signature(city_last_check)
            != _city_signature(city_pages[city_last])
        ):
            raise BusanBusanjinContractError("first/final boundary changed")
    except Exception as exc:
        meta["configured_collection_error"] = f"stability recheck: {_clean(exc)}"
        return [], BUSAN_BUSANJIN_PARSER, meta

    safe_rows: list[dict[str, Any]] = []
    privacy_redactions = 0
    for row in enriched:
        safe, count = _sanitize_row(row)
        safe_rows.append(safe)
        privacy_redactions += count
    deduper = dedupe_rows or _default_dedupe
    result = list(deduper(safe_rows))
    if len(result) != len(safe_rows):
        meta["configured_collection_error"] = (
            f"dedupe changed atomic row count {len(safe_rows)} to {len(result)}"
        )
        return [], BUSAN_BUSANJIN_PARSER, meta

    unique_source_rows = len(local_rows) + len(native_rows) + len(city_rows)
    status_counts = Counter(_clean(row.get("status")) for row in result)
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    meta.update(
        {
            "network_requests": budget.count,
            "required_list_requests": meta["list_requests"],
            "sentinel_requests": 3,
            "district_source_rows": len(local_rows),
            "district_data_pages": local_last,
            "district_page_counts": {
                page: len(rows)
                for page, rows in local_pages.items()
                if page <= local_last
            },
            "district_current_count": len(local_current),
            "platform_source_rows": len(first_platform),
            "platform_native_rows": len(native_rows),
            "platform_native_current_count": len(native_current),
            "platform_external_duplicate_rows": len(external_rows),
            "platform_external_unmatched_rows": 0,
            "platform_semantic_censuses": 2,
            "city_source_rows": len(city_rows),
            "city_data_pages": city_last,
            "city_page_counts": {
                page: len(rows)
                for page, rows in city_pages.items()
                if page <= city_last
            },
            "city_current_count": len(city_current),
            "source_total": len(local_rows) + len(first_platform) + len(city_rows),
            "source_rows": len(local_rows) + len(first_platform) + len(city_rows),
            "unique_education_source_rows": unique_source_rows,
            "current_source_count": len(current_rows),
            "expired_count": unique_source_rows - len(current_rows),
            "non_current_count": unique_source_rows - len(current_rows),
            "returned_count": len(result),
            "detail_pages": len(details.values),
            "detail_errors": 0,
            "application_control_count": sum(
                bool(row.get("reservation_available")) for row in result
            ),
            "offline_application_count": sum(
                row.get("application_type") == "OFFLINE_APPLY" for row in result
            ),
            "status_counts": dict(status_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "duplicate_source_identity_count": len(external_rows),
            "privacy_redactions": privacy_redactions,
            "pagination_detected": True,
            "pagination_complete": True,
            "details_complete": True,
            "snapshot_complete": True,
            "atomic_union_complete": True,
            "source_cap_reached": False,
            "configured_collection_error": "",
        }
    )
    return result, BUSAN_BUSANJIN_PARSER, meta


collect_courses = collect_busan_busanjin_education


__all__ = [
    "BUSAN_BUSANJIN_PROVIDER",
    "BUSAN_CITY_BUSANJIN_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_BUSANJIN_MUNICIPALITY_CODE",
    "BUSAN_BUSANJIN_MUNICIPALITY_NAME",
    "BUSAN_BUSANJIN_REGISTERED_URL",
    "BUSAN_BUSANJIN_CANONICAL_URL",
    "BUSAN_CITY_BUSANJIN_URL",
    "BUSAN_LIFELONG_BUSANJIN_OFFICE",
    "BUSAN_BUSANJIN_PARSER",
    "BUSAN_BUSANJIN_OWNERSHIP_SCOPE",
    "BUSAN_BUSANJIN_CANDIDATE_IDS",
    "BUSAN_BUSANJIN_OWNER_BOUNDARY_AUDIT",
    "BUSAN_BUSANJIN_DISCOVERY_AUDIT",
    "BusanBusanjinContractError",
    "is_busan_busanjin_education_target",
    "is_target",
    "busan_busanjin_list_url",
    "busan_busanjin_detail_url",
    "busan_busanjin_city_list_url",
    "busan_busanjin_city_detail_url",
    "busan_busanjin_lifelong_list_url",
    "busan_busanjin_lifelong_detail_url",
    "canonical_busan_busanjin_course_identity",
    "collect_busan_busanjin_education",
    "collect_courses",
]
