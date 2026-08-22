"""Atomic education collector for Busan Yeongdo-gu's official ledgers.

The district-owned ``/reserve/01785/01791.web`` page is the canonical
``강좌·교육 > 전체`` catalogue.  It is intentionally broader than the
lifelong-learning notice board and the individual facility tabs: the list
declares every education record and exposes a stable numeric ``idx``.

Two companion sources are part of the same municipal snapshot.  The Busan
integrated-reservation partition is fixed to Yeongdo-gu (``srchGugun=14``)
and resident councils (``srchResveInsttCd=33``).  The Busan Lifelong Learning
Platform office ``OFFICE_00002680`` contains both native ``LEARNING_*`` rows
and external Yeongdo reservation links.  External rows are suppressed only
after their ``lecIdx`` is proved to be the same numeric identity as a row in
the canonical district catalogue; native platform rows remain independent.

Every declared page, the immediate post-final sentinel, stable district/city
boundaries, two agreeing complete platform censuses, and every current/future
detail are required.  A failure in any one ledger discards the union.
Application pages, reservation-history pages,
attachments, free-form descriptions, instructor/contact values, and
applicant data are never fetched or persisted.
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
from service_group import (
    SERVICE_GROUP_EXPERIENCE,
    SERVICE_GROUP_PUBLIC_COURSE,
    infer_experience_institution_source_group,
)


BUSAN_YEONGDO_PROVIDER = "MUNI_WWW_YEONGDO_GO_KR_33400564"
BUSAN_YEONGDO_NOTICE_PROVIDER = "MUNI_WWW_YEONGDO_GO_KR_85BB5856"
BUSAN_CITY_YEONGDO_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_FD43BAD0"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_YEONGDO_MUNICIPALITY_CODE = "2620000000"
BUSAN_YEONGDO_MUNICIPALITY_NAME = "부산광역시 영도구"
BUSAN_YEONGDO_HOST = "www.yeongdo.go.kr"
BUSAN_YEONGDO_LIST_PATH = "/reserve/01785/01791.web"
BUSAN_YEONGDO_GENERAL_PATH = "/reserve/01785/01792/01793.web"
BUSAN_YEONGDO_URL = f"https://{BUSAN_YEONGDO_HOST}{BUSAN_YEONGDO_LIST_PATH}"
BUSAN_YEONGDO_CANONICAL_URL = BUSAN_YEONGDO_URL
BUSAN_YEONGDO_NOTICE_URL = "https://www.yeongdo.go.kr/hll/01419/01420.web"
BUSAN_YEONGDO_LIBRARY_INFO_URL = (
    "https://www.yeongdo.go.kr/library/01281/01282.web"
)
BUSAN_YEONGDO_RESERVATION_HISTORY_PATH = "/reserve/01790/02024.web"

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_YEONGDO_GUGUN = "14"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_YEONGDO_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_YEONGDO_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_LIFELONG_YEONGDO_OFFICE = "OFFICE_00002680"
BUSAN_LIFELONG_YEONGDO_OFFICE_NAME = "영도구청"
BUSAN_LIFELONG_YEONGDO_PAGE_SIZE = 1000
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_OFFICE_URL = _lifelong.BUSAN_LIFELONG_URL

BUSAN_YEONGDO_PAGE_SIZE = 10
BUSAN_YEONGDO_FETCH_ATTEMPTS = 3
BUSAN_YEONGDO_MAX_WORKERS = 8
BUSAN_YEONGDO_MAX_HTML_BYTES = 10_000_000
BUSAN_YEONGDO_MIN_REQUEST_TIMEOUT_SECONDS = 150
BUSAN_YEONGDO_SAFE_TOTAL_TIMEOUT_SECONDS = 240
BUSAN_YEONGDO_PARSER = (
    "busan_yeongdo_complete_education_idx_pages+clamped_last_sentinel+"
    "stable_first_last+busan_reserve_gugun14_office33_complete+empty_sentinel+"
    "lifelong_office00002680_pageunit1000_two_stable_complete_censuses+"
    "external_lecidx_duplicate_suppression+"
    "native_learning_current_details+identity_bound_apply_no_form_fetch+"
    "pii_allowlist+atomic_three_ledger_snapshot"
)
BUSAN_YEONGDO_OWNERSHIP_SCOPE = (
    "yeongdo_complete_education_catalogue_resident_councils_and_native_"
    "lifelong_platform_courses"
)

BUSAN_YEONGDO_CANDIDATE_IDS: Mapping[str, str] = {
    "busan_resident_councils": "MUNI_IR_1B09CCAFC09F",
    "library_information_page": "MUNI_IR_AC2C4BB4CB1C",
    "third_party_directory": "MUNI_IR_C3EFB530573A",
    "lifelong_notice_board": "MUNI_IR_C8D2702DE234",
}

BUSAN_YEONGDO_CANDIDATE_DECISIONS: Mapping[str, str] = {
    "MUNI_IR_1B09CCAFC09F": (
        "include_exact_gugun14_resident_council_education_partition"
    ),
    "MUNI_IR_AC2C4BB4CB1C": (
        "exclude_static_library_information_page_courses_are_in_complete_owner"
    ),
    "MUNI_IR_C3EFB530573A": "exclude_unofficial_third_party_directory",
    "MUNI_IR_C8D2702DE234": (
        "exclude_notice_board_as_ledger_use_complete_reservation_catalogue"
    ),
}

BUSAN_YEONGDO_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_YEONGDO_PROVIDER: {
        "decision": "canonical_complete_district_education_owner",
        "registered_url": BUSAN_YEONGDO_URL,
        "canonical_url": BUSAN_YEONGDO_CANONICAL_URL,
        "owner": BUSAN_YEONGDO_MUNICIPALITY_NAME,
    },
    BUSAN_YEONGDO_NOTICE_PROVIDER: {
        "decision": "exclude_notice_board_not_structured_course_ledger",
        "candidate_id": BUSAN_YEONGDO_CANDIDATE_IDS["lifelong_notice_board"],
        "url": BUSAN_YEONGDO_NOTICE_URL,
        "canonical_url": BUSAN_YEONGDO_CANONICAL_URL,
    },
    BUSAN_CITY_YEONGDO_PROVIDER: {
        "decision": "collect_exact_resident_council_partition_as_companion",
        "candidate_id": BUSAN_YEONGDO_CANDIDATE_IDS["busan_resident_councils"],
        "url": BUSAN_CITY_YEONGDO_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_YEONGDO_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "collect_native_learning_ids_suppress_external_idx_duplicates",
        "url": BUSAN_LIFELONG_OFFICE_URL,
        "office_code": BUSAN_LIFELONG_YEONGDO_OFFICE,
        "identity_rule": (
            "native LEARNING_* is independent; external lecIdx equals canonical idx"
        ),
    },
    "OFFICIAL_YEONGDO_EXPERIENCE_FACILITY": {
        "decision": "exclude_non_course_experience_facility_records",
        "branch": "실감형 체험공간 「폴짝폴짝」",
    },
    "OFFICIAL_YEONGDO_FACILITY_AND_PERFORMANCE_MENUS": {
        "decision": "exclude_non_education_menu_families",
        "reason": (
            "only the server-declared 강좌·교육 catalogue and education companions "
            "are in scope"
        ),
    },
}

BUSAN_YEONGDO_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_YEONGDO_CANONICAL_URL,
    "canonical_rows": 2306,
    "canonical_data_pages": 231,
    "canonical_page_counts": {"1-230": 10, "231": 6},
    "canonical_sentinel_page": 232,
    "canonical_sentinel_mode": "clamped_last",
    "canonical_non_course_rows": 28,
    "canonical_historical_reversed_range_rows": 5,
    "canonical_target_region_split_rows": 1,
    "canonical_current_education_rows": 45,
    "canonical_current_detail_rows": 45,
    "resident_url": BUSAN_CITY_YEONGDO_URL,
    "resident_rows": 8,
    "resident_data_pages": 1,
    "resident_sentinel_page": 2,
    "resident_current_rows": 8,
    "lifelong_office": BUSAN_LIFELONG_YEONGDO_OFFICE,
    "lifelong_rows": 884,
    "lifelong_default_page_size": 100,
    "lifelong_default_data_pages": 9,
    "lifelong_default_page_counts": {"1-8": 100, "9": 84},
    "lifelong_collector_page_size": BUSAN_LIFELONG_YEONGDO_PAGE_SIZE,
    "lifelong_data_pages": 1,
    "lifelong_page_counts": {"1": 884},
    "lifelong_sentinel_page": 2,
    "lifelong_native_rows": 66,
    "lifelong_native_current_rows": 40,
    "lifelong_external_rows": 818,
    "lifelong_external_unique_idx": 818,
    "lifelong_external_repeated_rows": 0,
    "lifelong_external_rows_matching_canonical_idx": 818,
    "lifelong_ordering": (
        "unstable_within_ties_resolved_by_exact_pageunit1000_full_census; "
        "require_two_equal_semantic_multisets"
    ),
    "atomic_current_rows": 93,
    "atomic_required_list_requests": 241,
    "atomic_required_detail_requests": 93,
    "atomic_required_requests_without_retries": 334,
    "conclusion": (
        "collect the complete district catalogue, exact resident-council partition, "
        "and native platform rows; suppress all identity-proved external platform "
        "republications"
    ),
}


class BusanYeongdoContractError(ValueError):
    """Raised when one of the audited source contracts changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport, response, or status-200 error-page failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Probe = Callable[[BeautifulSoup], None]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_ROOT_TOTAL_RE = re.compile(
    r"총\s*([\d,]+)\s*건의\s*교육이\s*있습니다\.\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)"
)
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*"
    r"(\d{1,2})(?:\.)?(?!\d)"
)
_ROOT_BRANCH_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_ROOT_ACTION_LABELS = {"신청하기", "대기자신청"}
_PHONE_RE = re.compile(
    r"(?<!\d)(?:010[-\s*]+[\d*]{3,4}[-\s*]+[\d*]{4}|"
    r"0\d{1,3}[-\s]?\d{3,4}[-\s]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_ROOT_STANDARD_LABELS = (
    "교육기간",
    "교육시간",
    "모집기간",
    "모집대상",
    "모집인원",
    "대기인원",
    "접수방법",
)
_ROOT_STANDARD_LABELS_NO_WAIT = tuple(
    value for value in _ROOT_STANDARD_LABELS if value != "대기인원"
)
_ROOT_DETAIL_LABELS = (
    "교육기간",
    "교육시간",
    "교육장소",
    "수강료",
    "준비물",
    "접수기간",
    "모집대상",
    "강사",
    "모집지역",
    "접수방법",
    "이용문의",
    "첨부파일",
)
_ROOT_DETAIL_SAFE_LABELS = frozenset(
    {
        "교육기간",
        "교육시간",
        "교육장소",
        "수강료",
        "접수기간",
        "모집대상",
        "모집지역",
        "접수방법",
    }
)
_ROOT_DETAIL_SKIPPED_LABELS = frozenset(_ROOT_DETAIL_LABELS) - (
    _ROOT_DETAIL_SAFE_LABELS
)

# These are the only rows in the 2026-07-22 complete census that use an event
# registration schema instead of an education-period schema.  Binding the
# exception to identities prevents a newly malformed course from being
# silently accepted.  They are contests, campus visits, and one-off briefing
# registrations rather than learner-course records.
_AUDITED_NON_COURSE_EVENT_IDS = frozenset(
    {
        "3503",
        "3504",
        "3505",
        "3444",
        "3445",
        "3446",
        "3447",
        "3361",
        "3158",
        "3157",
        "3156",
        "3159",
        "3160",
        "3161",
        "2965",
        "2564",
    }
)

# Exact source typos observed in the complete 2026-07-22 archive.  These
# records are long ended.  Only the bound identity, field, and original pair
# may be sorted; a new reversed range remains a hard contract failure.
_AUDITED_REVERSED_ROOT_RANGES: Mapping[tuple[str, str], tuple[str, str]] = {
    ("3359", "education"): ("2026-01-06", "2025-12-12"),
    ("1602", "education"): ("2022-06-27", "2022-06-10"),
    ("1365", "education"): ("2021-08-06", "2021-07-21"),
    ("1327", "application"): ("2021-07-08", "2021-07-01"),
    ("907", "education"): ("2019-10-17", "2019-10-02"),
}
_AUDITED_ROOT_TARGET_REGION_SPLITS: Mapping[
    str, tuple[str, str, str]
] = {
    # The list renders target and region in one 모집대상 <li>; the detail
    # table renders the same tokens as separate 모집대상/모집지역 rows.
    "3478": ("영도구민 (영도구)", "영도구민", "영도구"),
}
_EXCLUDED_EXPERIENCE_BRANCHES = frozenset({"실감형 체험공간 「폴짝폴짝」"})

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
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
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "대기접수": "WAITLIST",
    "대기자접수": "WAITLIST",
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
_PLATFORM_DETAIL_OPTIONAL_LABELS = frozenset({"수강료 기타"})
_PLATFORM_DETAIL_SAFE_LABELS = frozenset(
    {
        "강좌분류",
        "교육대상",
        "교육장소",
        "총 교육시간",
        "교육기간",
        "교육시간",
        "수강료",
        "재료비",
        "우선모집기간",
        "일반모집기간",
        "모집방법",
        "신청상태",
        "교육상태",
        "결제방법",
        "강좌제한",
        "수강료 기타",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    return _clean(node.get_text(" ", strip=True))


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanYeongdoContractError(
            f"expected one {label}, found {len(found)}"
        )
    return found[0]


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


def is_busan_yeongdo_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_YEONGDO_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == BUSAN_YEONGDO_HOST
        and parsed.port is None
        and parsed.path == BUSAN_YEONGDO_LIST_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


is_target = is_busan_yeongdo_education_target


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanYeongdoContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanYeongdoContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanYeongdoContractError(f"{label} must be positive")
    return result


def busan_yeongdo_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    if value == 1:
        return BUSAN_YEONGDO_URL
    return BUSAN_YEONGDO_URL + "?" + urlencode({"cpage": value})


def busan_yeongdo_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanYeongdoContractError("invalid Yeongdo course identity")
    return BUSAN_YEONGDO_URL + "?" + urlencode(
        (("amode", "view"), ("idx", value))
    )


def busan_yeongdo_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanYeongdoContractError("invalid Yeongdo application identity")
    return BUSAN_YEONGDO_URL + "?" + urlencode(
        (("amode", "ins"), ("lecIdx", value))
    )


def busan_yeongdo_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return (
        f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
        + urlencode(
            (
                ("curPage", value),
                ("srchGugun", BUSAN_CITY_YEONGDO_GUGUN),
                ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
            )
        )
    )


def busan_yeongdo_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanYeongdoContractError("invalid Busan city course identity")
    return (
        f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?"
        + urlencode((("resveGroupSn", group), ("progrmSn", program)))
    )


def busan_yeongdo_lifelong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_YEONGDO_OFFICE, value)
    payload["pageUnit"] = str(BUSAN_LIFELONG_YEONGDO_PAGE_SIZE)
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_yeongdo_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanYeongdoContractError("invalid lifelong course identity")
    return _lifelong.busan_lifelong_detail_url(value)


def canonical_busan_yeongdo_course_identity(value: Any) -> str:
    """Return ``idx:<n>`` for either canonical detail or platform alias URL."""

    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONGDO_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity = ""
    if (
        parsed.path == BUSAN_YEONGDO_LIST_PATH
        and set(query) == {"amode", "idx"}
        and query.get("amode") == ["view"]
    ):
        identity = _query_one(query, "idx")
    elif (
        parsed.path == BUSAN_YEONGDO_GENERAL_PATH
        and set(query) == {"amode", "lecIdx", "facCode"}
        and query.get("amode") == ["ins"]
        and query.get("facCode") == ["001"]
    ):
        identity = _query_one(query, "lecIdx")
    return f"idx:{identity}" if _IDENTITY_RE.fullmatch(identity) else ""


def _query_one(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    return _clean(values[0]) if len(values) == 1 else ""


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
                raise BusanYeongdoContractError(
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
        or final.path != requested.path
        or final.query != requested.query
        or final.username
        or final.password
        or final.params
        or final.fragment
    ):
        raise _TransientFetchError("source response URL changed scope")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if not content:
        raise _TransientFetchError("empty source response")
    if isinstance(content, bytes) and len(content) > BUSAN_YEONGDO_MAX_HTML_BYTES:
        raise _TransientFetchError("source HTML exceeds safety limit")
    if isinstance(content, str) and len(content.encode("utf-8")) > BUSAN_YEONGDO_MAX_HTML_BYTES:
        raise _TransientFetchError("source HTML exceeds safety limit")
    soup = BeautifulSoup(content, "lxml")
    if soup.select_one("title") is None:
        raise _TransientFetchError("status-200 response is not the expected HTML page")
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
        for attempt in range(1, BUSAN_YEONGDO_FETCH_ATTEMPTS + 1):
            try:
                budget.take()
                response = fetcher(thread_session(), url, timeout)
                soup, final_url = _response_soup(response, url)
                probe(soup)
                return key, (soup, final_url), attempt - 1
            except Exception as exc:
                messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
                if attempt < BUSAN_YEONGDO_FETCH_ATTEMPTS:
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
            raise BusanYeongdoContractError("invalid source date") from exc
    return result


def _date_range(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, str]:
    found = _dates(value)
    if not found and allow_empty:
        return "", ""
    if len(found) == 1:
        found.append(found[0])
    if len(found) != 2:
        raise BusanYeongdoContractError(f"{label} is not an exact date range")
    if found[1] < found[0]:
        raise BusanYeongdoContractError(f"{label} is reversed")
    return found[0].isoformat(), found[1].isoformat()


def _root_date_range(
    value: Any,
    label: str,
    *,
    identity: str,
    kind: str,
    allow_empty: bool = False,
) -> tuple[str, str, bool]:
    found = _dates(value)
    if not found and allow_empty:
        return "", "", False
    if len(found) == 1:
        found.append(found[0])
    if len(found) != 2:
        raise BusanYeongdoContractError(f"{label} is not an exact date range")
    corrected = False
    if found[1] < found[0]:
        original = (found[0].isoformat(), found[1].isoformat())
        if _AUDITED_REVERSED_ROOT_RANGES.get((identity, kind)) != original:
            raise BusanYeongdoContractError(f"{label} is reversed")
        found.sort()
        corrected = True
    return found[0].isoformat(), found[1].isoformat(), corrected


def _capacity(value: Any, label: str) -> tuple[Optional[int], Optional[int]]:
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)\s*명", _clean(value))
    if not match:
        raise BusanYeongdoContractError(f"{label} changed")
    return int(match.group(1)), int(match.group(2))


def _root_declaration(soup: BeautifulSoup, requested_page: int) -> tuple[int, int, int]:
    title = _one(soup.select("title"), "Yeongdo list title")
    if _text(title) != "전체 | 영도구 통합예약":
        raise BusanYeongdoContractError("Yeongdo list title changed")
    form = _one(soup.select("form#frmLecture[name='frmLecture']"), "frmLecture")
    action = urlparse(urljoin(BUSAN_YEONGDO_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "get"
        or action.scheme.lower() != "https"
        or (action.hostname or "").rstrip(".").lower() != BUSAN_YEONGDO_HOST
        or action.path != BUSAN_YEONGDO_LIST_PATH
    ):
        raise BusanYeongdoContractError("Yeongdo list form changed")
    fac = _one(form.select("input[name='facCode']"), "facCode field")
    search = _one(form.select("input[name='sstring']"), "sstring field")
    selector = _one(form.select("select[name='stype']"), "stype selector")
    options = [(_clean(node.get("value")), _text(node)) for node in selector.select("option")]
    if _clean(fac.get("value")) or _clean(search.get("value")) or options != [("title", "과정명")]:
        raise BusanYeongdoContractError("Yeongdo list is not the complete unfiltered scope")
    info = _one(
        soup.select("#body_content .infomenu1 .info1"),
        "declared education count",
    )
    match = _ROOT_TOTAL_RE.fullmatch(_text(info))
    if not match:
        raise BusanYeongdoContractError("Yeongdo education total declaration changed")
    total, current, last = (
        int(value.replace(",", "")) for value in match.groups()
    )
    if total < 1 or last != math.ceil(total / BUSAN_YEONGDO_PAGE_SIZE):
        raise BusanYeongdoContractError("Yeongdo total/page declaration is inconsistent")
    if requested_page <= last and current != requested_page:
        raise BusanYeongdoContractError("Yeongdo current page differs from request")
    if requested_page == last + 1 and current != last:
        raise BusanYeongdoContractError("Yeongdo sentinel did not clamp to final page")
    if requested_page > last + 1:
        raise BusanYeongdoContractError("Yeongdo request passed sentinel boundary")
    last_control = _one(
        soup.select("#body_content .pagination span.m.last > a"),
        "Yeongdo final-page control",
    )
    if _clean(last_control.get("title")) != "맨끝 페이지":
        raise BusanYeongdoContractError("Yeongdo final-page control changed")
    if current < last:
        if not last_control.has_attr("href"):
            raise BusanYeongdoContractError("Yeongdo final-page link disappeared")
        linked = urlparse(
            urljoin(BUSAN_YEONGDO_URL, _clean(last_control.get("href")))
        )
        linked_query = parse_qs(linked.query, keep_blank_values=True)
        if (
            linked.scheme.lower() != "https"
            or (linked.hostname or "").rstrip(".").lower() != BUSAN_YEONGDO_HOST
            or linked.path != BUSAN_YEONGDO_LIST_PATH
            or set(linked_query) != {"cpage"}
            or _query_one(linked_query, "cpage") != str(last)
        ):
            raise BusanYeongdoContractError("Yeongdo final-page control changed")
    elif last_control.has_attr("href"):
        raise BusanYeongdoContractError(
            "Yeongdo final-page control is not disabled on the final page"
        )
    return total, current, last


def _root_route_identity(
    href: Any,
    *,
    source_page: int,
    application: bool,
    expected: str = "",
) -> str:
    parsed = urlparse(urljoin(BUSAN_YEONGDO_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    identity_key = "lecIdx" if application else "idx"
    mode = "ins" if application else "view"
    expected_keys = {"amode", identity_key}
    if source_page > 1:
        expected_keys.add("cpage")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONGDO_HOST
        or parsed.port is not None
        or parsed.path != BUSAN_YEONGDO_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != expected_keys
        or query.get("amode") != [mode]
        or (source_page > 1 and query.get("cpage") != [str(source_page)])
    ):
        raise BusanYeongdoContractError(f"malformed Yeongdo {mode} route")
    identity = _query_one(query, identity_key)
    if not _IDENTITY_RE.fullmatch(identity) or (expected and identity != expected):
        raise BusanYeongdoContractError(f"Yeongdo {mode} identity mismatch")
    return identity


def _root_list_pairs(card: Tag) -> tuple[tuple[str, ...], dict[str, str]]:
    labels: list[str] = []
    values: dict[str, str] = {}
    for item in card.select(":scope .texts > ul > li"):
        text = _text(item)
        label, separator, value = text.partition(":")
        key = _clean(label)
        if not separator or not key or key in values:
            raise BusanYeongdoContractError("duplicate or malformed Yeongdo card field")
        labels.append(key)
        values[key] = _clean(value)
    return tuple(labels), values


def _root_controls(
    card: Tag, *, identity: str, source_page: int
) -> tuple[str, str, str, bool]:
    controls = card.select(":scope > .wrap1 > .btns > a")
    labels = tuple(_text(node) for node in controls)
    if labels in (("신청하기", "예약확인"), ("대기자신청", "예약확인")):
        first, history = controls
        _root_route_identity(
            first.get("href"),
            source_page=source_page,
            application=True,
            expected=identity,
        )
        history_url = urlparse(
            urljoin(BUSAN_YEONGDO_URL, _clean(history.get("href")))
        )
        if (
            history_url.scheme.lower() != "https"
            or (history_url.hostname or "").rstrip(".").lower()
            != BUSAN_YEONGDO_HOST
            or history_url.path != BUSAN_YEONGDO_RESERVATION_HISTORY_PATH
            or history_url.query
        ):
            raise BusanYeongdoContractError("reservation-history boundary changed")
        return "OPEN", labels[0], busan_yeongdo_application_url(identity), True
    if labels == ("접수대기",):
        if controls[0].has_attr("href"):
            raise BusanYeongdoContractError("scheduled course became actionable")
        return "SCHEDULED", labels[0], "", False
    if labels == ("접수마감",):
        href = _clean(controls[0].get("href"))
        if href not in {"", "#"}:
            raise BusanYeongdoContractError("closed course exposes an unknown route")
        return "CLOSED", labels[0], "", False
    if not labels:
        return "CLOSED", "", "", False
    raise BusanYeongdoContractError(f"unknown Yeongdo card controls {labels!r}")


def _root_branch(title: str) -> tuple[str, str]:
    match = _ROOT_BRANCH_RE.fullmatch(title)
    if not match:
        return "영도구 통합예약", title
    return _clean(match.group(1)), _clean(match.group(2))


def _apply_institution_service_metadata(row: dict[str, Any]) -> None:
    branch = _clean(row.get("branch"))
    source_group = infer_experience_institution_source_group(branch_name=branch)
    if not source_group:
        return
    is_library = source_group == "library"
    category = "도서관" if is_library else "박물관/과학관"
    row.update(
        {
            "collection_category": category,
            "domain_category": category,
            "source_group": source_group,
            "service_group": (
                SERVICE_GROUP_PUBLIC_COURSE
                if is_library
                else SERVICE_GROUP_EXPERIENCE
            ),
            "service_group_policy": "inferred" if is_library else "locked",
        }
    )
    raw_fields = row.get("raw_fields")
    if isinstance(raw_fields, dict):
        raw_fields["service_family"] = "education" if is_library else "experience"
        raw_fields["institution_source_group"] = source_group


def _parse_root_page(
    soup: BeautifulSoup, *, requested_page: int, expected_last: Optional[int] = None
) -> tuple[list[dict[str, Any]], int, int]:
    total, current, last = _root_declaration(soup, requested_page)
    if expected_last is not None and last != expected_last:
        raise BusanYeongdoContractError("Yeongdo displayed final page changed")
    cards = soup.select(
        "#body_content .list1f1t2b2 > ul.lst1 > li.li1"
    )
    expected_count = (
        BUSAN_YEONGDO_PAGE_SIZE
        if current < last
        else total - BUSAN_YEONGDO_PAGE_SIZE * (last - 1)
    )
    if len(cards) != expected_count:
        raise BusanYeongdoContractError(
            f"Yeongdo page {requested_page} row count changed"
        )
    rows: list[dict[str, Any]] = []
    for position, card in enumerate(cards, 1):
        link = _one(
            card.select(":scope > .wrap1 > a.col.a1[href]"),
            "Yeongdo detail link",
        )
        identity = _root_route_identity(
            link.get("href"), source_page=requested_page, application=False
        )
        heading = _one(
            link.select(":scope .texts > strong.t1"), "Yeongdo course title"
        )
        title = _text(heading)
        if not title:
            raise BusanYeongdoContractError("empty Yeongdo course title")
        branch, bare_title = _root_branch(title)
        labels, values = _root_list_pairs(link)
        status, control, application_url, active = _root_controls(
            card, identity=identity, source_page=requested_page
        )
        excluded_reason = ""
        if labels in (_ROOT_STANDARD_LABELS, _ROOT_STANDARD_LABELS_NO_WAIT):
            start, end, corrected_education_period = _root_date_range(
                values["교육기간"],
                f"Yeongdo {identity} education period",
                identity=identity,
                kind="education",
            )
            apply_start, apply_end, corrected_application_period = _root_date_range(
                values["모집기간"],
                f"Yeongdo {identity} application period",
                identity=identity,
                kind="application",
                allow_empty=True,
            )
            capacity_current, capacity_total = _capacity(
                values["모집인원"], f"Yeongdo {identity} capacity"
            )
            waiting_current: Optional[int] = None
            waiting_total: Optional[int] = None
            if "대기인원" in values:
                waiting_current, waiting_total = _capacity(
                    values["대기인원"], f"Yeongdo {identity} waiting capacity"
                )
            if branch in _EXCLUDED_EXPERIENCE_BRANCHES:
                excluded_reason = "non_course_experience_facility"
        else:
            if identity not in _AUDITED_NON_COURSE_EVENT_IDS:
                raise BusanYeongdoContractError(
                    f"Yeongdo {identity} introduced an unaudited card schema"
                )
            found_dates = _dates(" ".join(values.values()) + " " + title)
            if not found_dates:
                raise BusanYeongdoContractError(
                    f"audited non-course event {identity} lost its date"
                )
            start = min(found_dates).isoformat()
            end = max(found_dates).isoformat()
            apply_start = ""
            apply_end = ""
            corrected_education_period = False
            corrected_application_period = False
            capacity_current = None
            capacity_total = None
            waiting_current = None
            waiting_total = None
            excluded_reason = "non_course_event_registration_schema"
        row = {
            "provider": BUSAN_YEONGDO_PROVIDER,
            "provider_course_id": f"{BUSAN_YEONGDO_PROVIDER}:education:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "description": title,
            "branch": branch,
            "branch_code": (
                "yeongdo-"
                + hashlib.sha256(branch.encode("utf-8")).hexdigest()[:12]
            ),
            "preserve_branch": True,
            "category": "교육·강좌",
            "program_type": "교육/강좌",
            "raw_url": busan_yeongdo_detail_url(identity),
            "application_url": application_url,
            "application_type": (
                "WAITLIST_APPLY"
                if control == "대기자신청"
                else "ONLINE_RESERVATION"
                if active
                else "INFO_ONLY"
            ),
            "reservation_available": active,
            "status": status,
            "period": f"{start} ~ {end}",
            "start_date": start,
            "end_date": end,
            "apply_period": (
                f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
            ),
            "apply_start": apply_start,
            "apply_end": apply_end,
            "schedule_raw": values.get("교육시간", ""),
            "target": values.get("모집대상") or values.get("접수대상", ""),
            "fee": "",
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "venue_name": branch,
            "provider_organizer": branch,
            "municipality_code": BUSAN_YEONGDO_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_YEONGDO_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "영도구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": "complete_html_pages+current_detail_allowlist",
            "raw_fields": {
                "parser": BUSAN_YEONGDO_PARSER,
                "source_catalog": "yeongdo_complete_education_catalogue",
                "source_identity": identity,
                "source_page": current,
                "source_position": position,
                "source_title_without_branch": bare_title,
                "source_status_control": control,
                "source_application_control_identity_verified": active,
                "source_waiting_current": waiting_current,
                "source_waiting_total": waiting_total,
                "education_eligible": not excluded_reason,
                "education_exclusion_reason": excluded_reason,
                "source_reversed_education_period_corrected": (
                    corrected_education_period
                ),
                "source_reversed_application_period_corrected": (
                    corrected_application_period
                ),
                "detail_verified": False,
                "application_form_fetched": False,
                "reservation_history_fetched": False,
                "service_family": "education" if not excluded_reason else "excluded",
            },
        }
        _apply_institution_service_metadata(row)
        rows.append(row)
    return rows, total, last


def _root_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        repr(
            [
                (
                    _clean(row.get("raw_fields", {}).get("source_identity")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_status_control")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _safe_root_detail_values(table: Tag) -> tuple[dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for row in table.select(":scope > tbody > tr"):
        heading = _one(row.find_all("th", recursive=False), "root detail label")
        value = _one(row.find_all("td", recursive=False), "root detail value")
        label = _text(heading)
        labels.append(label)
        if label in _ROOT_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _ROOT_DETAIL_SKIPPED_LABELS:
            skipped.add(label)
        else:
            raise BusanYeongdoContractError(
                f"unknown Yeongdo detail field {label!r}"
            )
    if tuple(labels) != _ROOT_DETAIL_LABELS:
        raise BusanYeongdoContractError("Yeongdo detail field order changed")
    if skipped != _ROOT_DETAIL_SKIPPED_LABELS:
        raise BusanYeongdoContractError("Yeongdo skipped detail fields changed")
    return safe, skipped


def _root_target_matches(
    identity: Any,
    list_target: Any,
    detail_target: Any,
    detail_region: Any,
) -> tuple[bool, bool]:
    source = _clean(list_target)
    target = _clean(detail_target)
    region = _clean(detail_region)
    if source == target:
        return True, False
    expected = _AUDITED_ROOT_TARGET_REGION_SPLITS.get(_clean(identity))
    corrected = expected == (source, target, region)
    return corrected, corrected


def _parse_root_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    identity = _clean(parent.get("raw_fields", {}).get("source_identity"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONGDO_HOST
        or parsed.path != BUSAN_YEONGDO_LIST_PATH
        or set(query) != {"amode", "idx"}
        or query.get("amode") != ["view"]
        or query.get("idx") != [identity]
    ):
        raise BusanYeongdoContractError(f"Yeongdo detail {identity} response scope changed")
    if _text(_one(soup.select("title"), "Yeongdo detail title")) != "전체 | 영도구 통합예약":
        raise BusanYeongdoContractError("Yeongdo detail page title changed")
    root = _one(
        soup.select("#body_content .view1pic1info1"), "Yeongdo detail summary"
    )
    heading = _one(root.select(":scope h1.h1"), "Yeongdo detail course title")
    if _text(heading) != _clean(parent.get("title")):
        raise BusanYeongdoContractError(f"Yeongdo detail {identity} title mismatch")
    table = _one(root.select(":scope table.t3.ttvam"), "Yeongdo detail table")
    safe, _skipped = _safe_root_detail_values(table)
    detail_start, detail_end = _date_range(
        safe.get("교육기간"), f"Yeongdo detail {identity} education period"
    )
    if (detail_start, detail_end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanYeongdoContractError(f"Yeongdo detail {identity} dates mismatch")
    if parent.get("apply_start") and parent.get("apply_end"):
        apply_start, apply_end = _date_range(
            safe.get("접수기간"), f"Yeongdo detail {identity} application period"
        )
        if (apply_start, apply_end) != (
            _clean(parent.get("apply_start")),
            _clean(parent.get("apply_end")),
        ):
            raise BusanYeongdoContractError(
                f"Yeongdo detail {identity} application dates mismatch"
            )
    if _clean(safe.get("교육시간")) != _clean(parent.get("schedule_raw")):
        raise BusanYeongdoContractError(
            f"Yeongdo detail {identity} 교육시간 mismatch"
        )
    target_matches, target_region_split = _root_target_matches(
        identity,
        parent.get("target"),
        safe.get("모집대상"),
        safe.get("모집지역"),
    )
    if not target_matches:
        raise BusanYeongdoContractError(
            f"Yeongdo detail {identity} 모집대상 mismatch"
        )
    result = dict(parent)
    result.update(
        {
            "venue_name": safe.get("교육장소") or parent.get("branch"),
            "fee": safe.get("수강료", ""),
            "application_method_raw": safe.get("접수방법", ""),
        }
    )
    result["raw_fields"] = {
        **parent.get("raw_fields", {}),
        "detail_verified": True,
        "detail_safe_fields": tuple(sorted(_ROOT_DETAIL_SAFE_LABELS)),
        "instructor_value_never_read": True,
        "inquiry_value_never_read": True,
        "attachments_never_read": True,
        "preparation_never_read": True,
        "free_form_detail_never_read": True,
        "audited_target_region_structural_split": target_region_split,
        "application_form_fetched": False,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_YEONGDO_OFFICE
    )
    if (
        office is None
        or office.name != BUSAN_LIFELONG_YEONGDO_OFFICE_NAME
        or office.municipality_code
        or office.municipality_name
        or office.ownership != "duplicate_dedicated_yeongdo_owner"
    ):
        raise BusanYeongdoContractError("lifelong Yeongdo office ownership changed")
    return office


def _parse_platform_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    office = _platform_office()
    form_errors = _lifelong._form_errors(soup, office, page)
    if form_errors:
        raise BusanYeongdoContractError("; ".join(form_errors))
    last, last_errors = _lifelong._advertised_last(soup)
    if last_errors:
        raise BusanYeongdoContractError("; ".join(last_errors))
    if expected_last is not None and last != expected_last:
        raise BusanYeongdoContractError("lifelong displayed final page changed")
    rows, row_errors = _lifelong._parse_list_page(
        soup, office=office, page=page
    )
    if row_errors:
        raise BusanYeongdoContractError("; ".join(row_errors))
    return rows, last


def _platform_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return _lifelong._page_signature(rows)


def _platform_archive_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    """Hash the semantic multiset, ignoring unstable display positions."""

    values = sorted(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("schedule_raw")),
            _clean(row.get("fee")),
            _clean(row.get("capacity")),
            _clean(row.get("raw_fields", {}).get("source_status")),
            _clean(row.get("raw_fields", {}).get("selection_method")),
        )
        for row in rows
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _platform_external_idx(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONGDO_HOST
        or parsed.port is not None
        or parsed.path != BUSAN_YEONGDO_GENERAL_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) != {"amode", "lecIdx", "facCode"}
        or query.get("amode") != ["ins"]
        or query.get("facCode") != ["001"]
    ):
        raise BusanYeongdoContractError(
            "lifelong external row left the canonical Yeongdo alias scope"
        )
    identity = _query_one(query, "lecIdx")
    if not _IDENTITY_RE.fullmatch(identity):
        raise BusanYeongdoContractError("lifelong external lecIdx is malformed")
    return identity


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(
        identity
    ):
        raise BusanYeongdoContractError("invalid native lifelong identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_YEONGDO_PROVIDER,
            "provider_course_id": (
                f"{BUSAN_YEONGDO_PROVIDER}:lifelong:{identity}"
            ),
            "branch": BUSAN_LIFELONG_YEONGDO_OFFICE_NAME,
            "branch_code": "yeongdo-lifelong-office00002680",
            "preserve_branch": True,
            "provider_organizer": BUSAN_LIFELONG_YEONGDO_OFFICE_NAME,
            "municipality_code": BUSAN_YEONGDO_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_YEONGDO_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "영도구",
            "collection_type": "complete_shared_office_pages+native_current_detail",
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_YEONGDO_PARSER,
        "source_catalog": "busan_lifelong_yeongdo_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
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
            raise BusanYeongdoContractError(
                f"unknown or duplicate lifelong detail field {label!r}"
            )
        labels.append(label)
        if label in _PLATFORM_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = list(_PLATFORM_DETAIL_REQUIRED_LABELS)
    without_optional = [label for label in labels if label != "수강료 기타"]
    if without_optional != required:
        raise BusanYeongdoContractError("lifelong detail field order changed")
    expected_skipped = set(required) - set(_PLATFORM_DETAIL_SAFE_LABELS)
    if skipped != expected_skipped:
        raise BusanYeongdoContractError("lifelong private/free detail boundary changed")
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
        or parsed.path != BUSAN_LIFELONG_DETAIL_PATH
        or set(query) != {"lng_id"}
        or query.get("lng_id") != [identity]
    ):
        raise BusanYeongdoContractError(
            f"lifelong detail {identity} response scope changed"
        )
    form = _one(
        soup.select("form#learningVO[name='learningVO']"),
        "lifelong detail form",
    )
    action = urlparse(
        urljoin(final_url, _clean(form.get("action")))
    )
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != BUSAN_LIFELONG_DETAIL_PATH
        or parse_qs(action.query, keep_blank_values=True).get("lng_id")
        != [identity]
    ):
        raise BusanYeongdoContractError("lifelong detail form changed")
    identity_fields = {
        _clean(node.get("value")) for node in form.select("input[name='lng_id']")
    }
    office_fields = {
        _clean(node.get("value")) for node in form.select("input[name='inst_id']")
    }
    if identity_fields != {identity} or office_fields != {
        BUSAN_LIFELONG_YEONGDO_OFFICE
    }:
        raise BusanYeongdoContractError(
            f"lifelong detail {identity} identity/office mismatch"
        )
    heading = _one(soup.select("h2.enrolTit"), "lifelong detail heading")
    prefix = _one(heading.select(":scope > span"), "lifelong office prefix")
    if _text(prefix) != f"[{BUSAN_LIFELONG_YEONGDO_OFFICE_NAME}]":
        raise BusanYeongdoContractError("lifelong detail office prefix changed")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")):
        raise BusanYeongdoContractError(
            f"lifelong detail {identity} title mismatch"
        )
    _labels, safe, _skipped = _safe_platform_detail_values(soup)
    detail_start, detail_end = _date_range(
        safe.get("교육기간"), f"lifelong detail {identity} education period"
    )
    if (detail_start, detail_end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanYeongdoContractError(
            f"lifelong detail {identity} education dates mismatch"
        )
    if parent.get("apply_start") and parent.get("apply_end"):
        detail_apply_start, detail_apply_end = _date_range(
            safe.get("일반모집기간"),
            f"lifelong detail {identity} application period",
        )
        if (detail_apply_start, detail_apply_end) != (
            _clean(parent.get("apply_start")),
            _clean(parent.get("apply_end")),
        ):
            raise BusanYeongdoContractError(
                f"lifelong detail {identity} application dates mismatch"
            )
    control_nodes = soup.select("#learning_aply_btn")
    source_status = _clean(raw.get("source_status"))
    active = source_status == "접수중"
    if active:
        control = _one(control_nodes, "lifelong application control")
        control_label = _text(control)
        if (
            control_label not in {"일반모집신청", "수강신청", "대기자신청"}
            or _clean(control.get("onclick"))
            != "fn_learning_apply(); return false;"
        ):
            raise BusanYeongdoContractError(
                f"lifelong detail {identity} application control changed"
            )
    else:
        if control_nodes:
            raise BusanYeongdoContractError(
                f"closed lifelong detail {identity} became actionable"
            )
        control_label = ""
    result = dict(parent)
    result.update(
        {
            "status": "OPEN" if active else "CLOSED",
            "application_url": (
                busan_yeongdo_lifelong_detail_url(identity) if active else ""
            ),
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
            "target": safe.get("교육대상", ""),
            "venue_name": safe.get("교육장소") or BUSAN_LIFELONG_YEONGDO_OFFICE_NAME,
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
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, *, page: int
) -> tuple[int, Optional[Tag]]:
    if _text(_one(soup.select("title"), "Busan city list title")) != _CITY_LIST_TITLE:
        raise BusanYeongdoContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanYeongdoContractError("Busan city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "city curPage field")
    if _clean(page_field.get("value")) != str(page):
        raise BusanYeongdoContractError("Busan city form page differs from request")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_YEONGDO_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanYeongdoContractError(f"Busan city {name} owner filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "city last page")
    parsed = urlparse(
        urljoin(BUSAN_CITY_YEONGDO_URL, _clean(end_link.get("href")))
    )
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.path != BUSAN_CITY_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or query.get("srchGugun") != [BUSAN_CITY_YEONGDO_GUGUN]
        or query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
    ):
        raise BusanYeongdoContractError("unsafe Busan city final-page control")
    last_raw = _query_one(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanYeongdoContractError("invalid Busan city final page")
    last = int(last_raw)
    roots = soup.select("ul.reserveList")
    if page <= last:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last + 1:
        if roots:
            raise BusanYeongdoContractError(
                "Busan city sentinel unexpectedly retained a reserve list"
            )
        root = None
    else:
        raise BusanYeongdoContractError("Busan city request passed sentinel boundary")
    return last, root


def _city_card_date_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanYeongdoContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (
            date.fromisoformat(part) for part in match.groups()
        )
    except ValueError as exc:
        raise BusanYeongdoContractError(f"{label} contains an invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanYeongdoContractError(f"{label} is reversed")
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
    last, root = _city_list_contract(soup, page=page)
    if expected_last is not None and last != expected_last:
        raise BusanYeongdoContractError("Busan city displayed final page changed")
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"), "Busan city course link"
        )
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanYeongdoContractError(
                f"Busan city page {page} row {position}: identity action changed"
            )
        group_id, program_id = action.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city course title")
        title = _text(title_node)
        if not title or _clean(title_node.get("title")) != title:
            raise BusanYeongdoContractError("Busan city card title changed")
        source_status = _text(
            _one(link.select(":scope .statusMark"), "Busan city status")
        )
        if source_status not in _CITY_STATUS_MAP:
            raise BusanYeongdoContractError("unknown Busan city source status")
        definitions = _one(
            link.select(":scope .infoBox > dl"), "Busan city card values"
        )
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanYeongdoContractError("Busan city card labels changed")
        # The final inquiry value is deliberately not read.
        safe = {
            label: _text(value)
            for label, value in zip(labels[:-1], values[:-1])
        }
        if any(not value for value in safe.values()):
            raise BusanYeongdoContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if (
            not branch.startswith("영도구 ")
            or not branch.endswith(" 주민자치회")
        ):
            raise BusanYeongdoContractError("Busan city course left Yeongdo owner")
        apply_start, apply_end, start, end = _city_card_date_ranges(
            safe["일자"], f"Busan city page {page} row {position} dates"
        )
        raw_url = busan_yeongdo_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_YEONGDO_PROVIDER,
                "provider_course_id": f"{BUSAN_YEONGDO_PROVIDER}:reserve:{group_id}:{program_id}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"yeongdo-reserve-{group_id}",
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
                "capacity_total": None,
                "capacity_current": None,
                "venue_name": safe["장소"],
                "provider_organizer": branch,
                "municipality_code": BUSAN_YEONGDO_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_YEONGDO_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "영도구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_YEONGDO_PARSER,
                    "source_catalog": "busan_reserve_yeongdo_resident_councils",
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
        raise BusanYeongdoContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(part) for part in found)
    except ValueError as exc:
        raise BusanYeongdoContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BusanYeongdoContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _city_application_methods(value: Any) -> tuple[str, ...]:
    return tuple(
        cleaned
        for part in _clean(value).split(",")
        if (cleaned := _clean(part))
    )


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
            raise BusanYeongdoContractError("duplicate Busan city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _CITY_DETAIL_SKIPPED_LABELS:
            skipped.add(label)
        else:
            raise BusanYeongdoContractError(
                f"unknown Busan city detail field {label!r}"
            )
    without_attachment = [label for label in labels if label != "첨부파일"]
    if tuple(without_attachment) != _CITY_DETAIL_REQUIRED_LABELS:
        raise BusanYeongdoContractError("Busan city detail field order changed")
    if "문의전화" not in skipped:
        raise BusanYeongdoContractError("Busan city inquiry boundary changed")
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
        or parsed.path != BUSAN_CITY_DETAIL_PATH
        or set(query) != {"resveGroupSn", "progrmSn"}
        or query.get("resveGroupSn") != [group_id]
        or query.get("progrmSn") != [program_id]
    ):
        raise BusanYeongdoContractError("Busan city detail response scope changed")
    if _text(_one(soup.select("title"), "Busan city detail title")) != _CITY_LIST_TITLE:
        raise BusanYeongdoContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanYeongdoContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanYeongdoContractError("Busan city detail identity changed")
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
        raise BusanYeongdoContractError("Busan city list/detail title mismatch")
    if (
        source_status not in _CITY_STATUS_MAP
        or _CITY_STATUS_MAP[source_status]
        != _CITY_STATUS_MAP.get(_clean(raw.get("source_status")))
    ):
        raise BusanYeongdoContractError("Busan city list/detail status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    _labels, safe, skipped = _safe_city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanYeongdoContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanYeongdoContractError("Busan city free-form boundary changed")
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
        raise BusanYeongdoContractError("Busan city list/detail dates mismatch")
    for label, key in (
        ("신청방법", "source_application_method"),
        ("운영기관", "branch"),
        ("대상", "target"),
    ):
        expected = raw.get(key) if key == "source_application_method" else parent.get(key)
        matches = (
            _city_application_methods(safe[label])
            == _city_application_methods(expected)
            if label == "신청방법"
            else _clean(safe[label]) == _clean(expected)
        )
        if not matches:
            raise BusanYeongdoContractError(f"Busan city list/detail {label} mismatch")
    controls = form.select(
        ":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL"
    )
    normalized_status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    control_label = _text(controls[0]) if len(controls) == 1 else ""
    if len(controls) > 1:
        raise BusanYeongdoContractError("multiple Busan city application controls")
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if len(controls) != 1 or not any(
                token in control_label for token in ("신청", "예약")
            ):
                raise BusanYeongdoContractError(
                    "open Busan city course lacks active identity-bound control"
                )
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("방문", "전화")):
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanYeongdoContractError("unknown Busan city application method")
    elif normalized_status == "WAITLIST":
        if (
            "온라인" not in method
            or len(controls) != 1
            or not any(token in control_label for token in ("대기예약", "대기신청"))
        ):
            raise BusanYeongdoContractError(
                "wait-list Busan city course lacks active identity-bound control"
            )
        active = True
        application_type = "WAITLIST_APPLY"
    elif normalized_status == "CLOSED":
        if control_label not in {"", "접수마감"}:
            raise BusanYeongdoContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED":
        if control_label not in {"", "대기중", "접수대기"}:
            raise BusanYeongdoContractError("scheduled Busan city control changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
            "application_method_raw": method,
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


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _sanitize_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redactions = 0

    def visit(value: Any) -> Any:
        nonlocal redactions
        if isinstance(value, Mapping):
            return {str(key): visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if isinstance(value, str):
            updated, phones = _PHONE_RE.subn("[redacted]", value)
            updated, emails = _EMAIL_RE.subn("[redacted]", updated)
            redactions += phones + emails
            return updated
        return value

    return visit(row), redactions


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "root_source_rows": 0,
        "root_data_pages": 0,
        "root_page_counts": {},
        "root_current_count": 0,
        "root_non_education_count": 0,
        "root_exclusion_counts": {},
        "root_branch_counts": {},
        "root_current_branch_counts": {},
        "platform_source_rows": 0,
        "platform_data_pages": 0,
        "platform_page_counts": {},
        "platform_native_rows": 0,
        "platform_native_current_count": 0,
        "platform_external_duplicate_rows": 0,
        "platform_external_unique_identities": 0,
        "platform_external_repeated_rows": 0,
        "platform_external_unmatched_rows": 0,
        "platform_initial_census_changed": False,
        "city_source_rows": 0,
        "city_data_pages": 0,
        "city_page_counts": {},
        "city_current_count": 0,
        "city_branch_counts": {},
        "source_total": 0,
        "source_rows": 0,
        "unique_education_source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "application_control_count": 0,
        "status_counts": {},
        "branch_count": 0,
        "branch_counts": {},
        "duplicate_source_identity_count": 0,
        "privacy_redactions": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "no_current_reason": "",
        "configured_collection_error": "",
        "municipality_code": BUSAN_YEONGDO_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_YEONGDO_MUNICIPALITY_NAME,
        "canonical_url": BUSAN_YEONGDO_CANONICAL_URL,
        "ownership_scope": BUSAN_YEONGDO_OWNERSHIP_SCOPE,
        "candidate_ids": dict(BUSAN_YEONGDO_CANDIDATE_IDS),
        "candidate_decisions": dict(BUSAN_YEONGDO_CANDIDATE_DECISIONS),
        "owner_boundary_audit": dict(BUSAN_YEONGDO_OWNER_BOUNDARY_AUDIT),
    }


def collect_busan_yeongdo_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 260,
    detail_limit: int = 160,
    max_requests: int = 450,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_YEONGDO_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future snapshot of all three ledgers."""

    meta = _base_meta()
    if not is_busan_yeongdo_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Busan Yeongdo education owner"
        )
        return [], BUSAN_YEONGDO_PARSER, meta
    try:
        if any(
            isinstance(value, bool)
            for value in (timeout, max_pages, detail_limit, max_requests, max_workers)
        ):
            raise ValueError("boolean limits are invalid")
        # Each bounded lifelong-learning page is fetched in two complete
        # censuses for a semantic stability check.
        request_timeout = max(
            BUSAN_YEONGDO_MIN_REQUEST_TIMEOUT_SECONDS,
            int(timeout),
        )
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_YEONGDO_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_YEONGDO_PARSER, meta
    if page_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"max_pages cap allows {page_cap} of at least 3 ledger data pages"
        )
        return [], BUSAN_YEONGDO_PARSER, meta
    if request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"max_requests cap allows {request_cap} of 3 first-ledger requests"
        )
        return [], BUSAN_YEONGDO_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def record_fetch(result: _FetchResult, *, list_phase: bool) -> None:
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count
        if list_phase:
            meta["list_requests"] += len(result.values)
            meta["pages"] += len(result.values)

    def root_first_probe(soup: BeautifulSoup) -> None:
        _parse_root_page(soup, requested_page=1)

    def platform_first_probe(soup: BeautifulSoup) -> None:
        _parse_platform_page(soup, page=1)

    def city_first_probe(soup: BeautifulSoup) -> None:
        _parse_city_page(soup, page=1)

    first = _fetch_many(
        (
            ("root", busan_yeongdo_list_url(1), root_first_probe),
            (
                "platform",
                busan_yeongdo_lifelong_list_url(1),
                platform_first_probe,
            ),
            ("city", busan_yeongdo_city_list_url(1), city_first_probe),
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=min(3, workers),
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(first, list_phase=True)
    if first.errors or set(first.values) != {"root", "platform", "city"}:
        meta["configured_collection_error"] = "; ".join(first.errors) or (
            "missing one or more first-ledger responses"
        )
        return [], BUSAN_YEONGDO_PARSER, meta

    try:
        root_first_rows, root_total, root_last = _parse_root_page(
            first.values["root"][0], requested_page=1
        )
        platform_first_rows, platform_last = _parse_platform_page(
            first.values["platform"][0], page=1
        )
        city_first_rows, city_last = _parse_city_page(
            first.values["city"][0], page=1
        )
        if not platform_first_rows:
            raise BusanYeongdoContractError(
                "lifelong Yeongdo office unexpectedly has no archive rows"
            )
        platform_total = int(
            platform_first_rows[0].get("raw_fields", {}).get("list_sequence") or 0
        )
        if (
            platform_total < 1
            or platform_last
            != math.ceil(platform_total / BUSAN_LIFELONG_YEONGDO_PAGE_SIZE)
        ):
            raise BusanYeongdoContractError(
                "lifelong sequence total/final-page declaration is inconsistent"
            )
        data_page_count = root_last + platform_last + city_last
        # The shared platform has nondeterministic ordering within tied result
        # groups.  Its initial complete census and one post-discovery complete
        # census must agree as a semantic multiset.  District and city still
        # require two boundary rechecks after those censuses.
        required_list_requests = data_page_count + 3 + platform_last + 4
        meta.update(
            {
                "root_data_pages": root_last,
                "platform_data_pages": platform_last,
                "city_data_pages": city_last,
                "required_list_requests": required_list_requests,
            }
        )
        if data_page_count > page_cap:
            raise BusanYeongdoContractError(
                f"max_pages cap allows {page_cap} of {data_page_count} declared data pages"
            )
        if required_list_requests > request_cap:
            raise BusanYeongdoContractError(
                f"max_requests cap allows {request_cap} of at least "
                f"{required_list_requests} required list requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"first-page contract: {_clean(exc)}"
        return [], BUSAN_YEONGDO_PARSER, meta

    jobs: list[tuple[Any, str, Probe]] = []
    for page in range(2, root_last + 1):
        jobs.append(
            (
                ("root", page),
                busan_yeongdo_list_url(page),
                lambda soup, page=page: _parse_root_page(
                    soup, requested_page=page, expected_last=root_last
                ),
            )
        )
    jobs.append(
        (
            ("root", root_last + 1),
            busan_yeongdo_list_url(root_last + 1),
            lambda soup: _parse_root_page(
                soup, requested_page=root_last + 1, expected_last=root_last
            ),
        )
    )
    for page in range(2, platform_last + 1):
        jobs.append(
            (
                ("platform", page),
                busan_yeongdo_lifelong_list_url(page),
                lambda soup, page=page: _parse_platform_page(
                    soup, page=page, expected_last=platform_last
                ),
            )
        )
    jobs.append(
        (
            ("platform", platform_last + 1),
            busan_yeongdo_lifelong_list_url(platform_last + 1),
            lambda soup: _parse_platform_page(
                soup, page=platform_last + 1, expected_last=platform_last
            ),
        )
    )
    for page in range(2, city_last + 1):
        jobs.append(
            (
                ("city", page),
                busan_yeongdo_city_list_url(page),
                lambda soup, page=page: _parse_city_page(
                    soup, page=page, expected_last=city_last
                ),
            )
        )
    jobs.append(
        (
            ("city", city_last + 1),
            busan_yeongdo_city_list_url(city_last + 1),
            lambda soup: _parse_city_page(
                soup, page=city_last + 1, expected_last=city_last
            ),
        )
    )
    remaining = _fetch_many(
        jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(remaining, list_phase=True)
    meta["sentinel_requests"] = sum(
        key in remaining.values
        for key in (
            ("root", root_last + 1),
            ("platform", platform_last + 1),
            ("city", city_last + 1),
        )
    )
    if remaining.errors or len(remaining.values) != len(jobs):
        meta["configured_collection_error"] = "; ".join(remaining.errors) or (
            "missing complete archive/sentinel response"
        )
        return [], BUSAN_YEONGDO_PARSER, meta

    try:
        root_pages: dict[int, list[dict[str, Any]]] = {1: root_first_rows}
        root_page_counts: dict[int, int] = {1: len(root_first_rows)}
        for page in range(2, root_last + 1):
            rows, declared, _last = _parse_root_page(
                remaining.values[("root", page)][0],
                requested_page=page,
                expected_last=root_last,
            )
            if declared != root_total:
                raise BusanYeongdoContractError("Yeongdo declared total changed by page")
            root_pages[page] = rows
            root_page_counts[page] = len(rows)
        root_sentinel, sentinel_total, _sentinel_last = _parse_root_page(
            remaining.values[("root", root_last + 1)][0],
            requested_page=root_last + 1,
            expected_last=root_last,
        )
        if sentinel_total != root_total or _root_signature(root_sentinel) != _root_signature(
            root_pages[root_last]
        ):
            raise BusanYeongdoContractError(
                "Yeongdo immediate post-final page is not exact clamped final page"
            )
        root_rows = [row for page in range(1, root_last + 1) for row in root_pages[page]]
        if len(root_rows) != root_total:
            raise BusanYeongdoContractError("Yeongdo parsed row count differs from total")
        root_ids = [
            _clean(row.get("raw_fields", {}).get("source_identity")) for row in root_rows
        ]
        if len(root_ids) != len(set(root_ids)):
            raise BusanYeongdoContractError("Yeongdo catalogue contains duplicate idx")
        root_signatures = [_root_signature(root_pages[page]) for page in root_pages]
        if len(root_signatures) != len(set(root_signatures)):
            raise BusanYeongdoContractError("Yeongdo catalogue repeated a data page")

        platform_pages: dict[int, list[dict[str, Any]]] = {1: platform_first_rows}
        platform_page_counts: dict[int, int] = {1: len(platform_first_rows)}
        for page in range(2, platform_last + 1):
            rows, _last = _parse_platform_page(
                remaining.values[("platform", page)][0],
                page=page,
                expected_last=platform_last,
            )
            platform_pages[page] = rows
            platform_page_counts[page] = len(rows)
        platform_sentinel, _last = _parse_platform_page(
            remaining.values[("platform", platform_last + 1)][0],
            page=platform_last + 1,
            expected_last=platform_last,
        )
        if platform_sentinel:
            raise BusanYeongdoContractError(
                "lifelong immediate post-final sentinel is not empty"
            )
        platform_rows = [
            row for page in range(1, platform_last + 1) for row in platform_pages[page]
        ]
        if len(platform_rows) != platform_total:
            raise BusanYeongdoContractError(
                "lifelong parsed row count differs from source sequence total"
            )
        sequences = [
            int(row.get("raw_fields", {}).get("list_sequence") or 0)
            for row in platform_rows
        ]
        if sequences != list(range(platform_total, 0, -1)):
            raise BusanYeongdoContractError("lifelong source sequence has a gap/reorder")
        platform_signatures = [
            _platform_signature(platform_pages[page]) for page in platform_pages
        ]
        if len(platform_signatures) != len(set(platform_signatures)):
            raise BusanYeongdoContractError("lifelong archive repeated a data page")

        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first_rows}
        city_page_counts: dict[int, int] = {1: len(city_first_rows)}
        for page in range(2, city_last + 1):
            rows, _last = _parse_city_page(
                remaining.values[("city", page)][0],
                page=page,
                expected_last=city_last,
            )
            city_pages[page] = rows
            city_page_counts[page] = len(rows)
        city_sentinel, _last = _parse_city_page(
            remaining.values[("city", city_last + 1)][0],
            page=city_last + 1,
            expected_last=city_last,
        )
        if city_sentinel:
            raise BusanYeongdoContractError(
                "Busan city immediate post-final sentinel is not empty"
            )
        city_rows = [row for page in range(1, city_last + 1) for row in city_pages[page]]
        city_ids = [
            _clean(row.get("raw_fields", {}).get("source_identity")) for row in city_rows
        ]
        if len(city_ids) != len(set(city_ids)):
            raise BusanYeongdoContractError("Busan city source identities are duplicated")
        city_signatures = [_city_signature(city_pages[page]) for page in city_pages]
        if len(city_signatures) != len(set(city_signatures)):
            raise BusanYeongdoContractError("Busan city archive repeated a data page")
    except Exception as exc:
        meta["configured_collection_error"] = f"complete archive: {_clean(exc)}"
        return [], BUSAN_YEONGDO_PARSER, meta

    def platform_sweep_jobs(label: str) -> list[tuple[Any, str, Probe]]:
        return [
            (
                (label, page),
                busan_yeongdo_lifelong_list_url(page),
                lambda soup, page=page: _parse_platform_page(
                    soup, page=page, expected_last=platform_last
                ),
            )
            for page in range(1, platform_last + 1)
        ]

    def platform_census(
        result: _FetchResult, label: str
    ) -> tuple[dict[int, list[dict[str, Any]]], dict[int, int], list[dict[str, Any]]]:
        pages: dict[int, list[dict[str, Any]]] = {}
        counts: dict[int, int] = {}
        for page in range(1, platform_last + 1):
            rows, _last = _parse_platform_page(
                result.values[(label, page)][0],
                page=page,
                expected_last=platform_last,
            )
            pages[page] = rows
            counts[page] = len(rows)
        rows = [row for page in range(1, platform_last + 1) for row in pages[page]]
        if len(rows) != platform_total:
            raise BusanYeongdoContractError(
                "lifelong stable census row count differs from source total"
            )
        sequences = [
            int(row.get("raw_fields", {}).get("list_sequence") or 0)
            for row in rows
        ]
        if sequences != list(range(platform_total, 0, -1)):
            raise BusanYeongdoContractError(
                "lifelong stable census sequence has a gap/reorder"
            )
        signatures = [_platform_signature(pages[page]) for page in pages]
        if len(signatures) != len(set(signatures)):
            raise BusanYeongdoContractError(
                "lifelong stable census repeated a data page"
            )
        return pages, counts, rows

    platform_second_jobs = platform_sweep_jobs("platform_second")
    platform_second = _fetch_many(
        platform_second_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(platform_second, list_phase=True)
    if platform_second.errors or len(platform_second.values) != platform_last:
        meta["configured_collection_error"] = "; ".join(
            platform_second.errors
        ) or (
            "missing one or more lifelong full-census stability responses"
        )
        return [], BUSAN_YEONGDO_PARSER, meta

    final_recheck_jobs = [
        (
            ("root", "first"),
            busan_yeongdo_list_url(1),
            lambda soup: _parse_root_page(
                soup, requested_page=1, expected_last=root_last
            ),
        ),
        (
            ("root", "last"),
            busan_yeongdo_list_url(root_last),
            lambda soup: _parse_root_page(
                soup, requested_page=root_last, expected_last=root_last
            ),
        ),
        (
            ("city", "first"),
            busan_yeongdo_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1, expected_last=city_last),
        ),
        (
            ("city", "last"),
            busan_yeongdo_city_list_url(city_last),
            lambda soup: _parse_city_page(
                soup, page=city_last, expected_last=city_last
            ),
        ),
    ]
    final_rechecks = _fetch_many(
        final_recheck_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(final_rechecks, list_phase=True)
    meta["stability_rechecks"] = len(platform_second.values) + len(final_rechecks.values)
    if final_rechecks.errors or len(final_rechecks.values) != len(
        final_recheck_jobs
    ):
        meta["configured_collection_error"] = "; ".join(
            final_rechecks.errors
        ) or "missing final boundary stability response"
        return [], BUSAN_YEONGDO_PARSER, meta
    try:
        platform_second_pages, platform_second_counts, platform_second_rows = platform_census(
            platform_second, "platform_second"
        )
        meta["platform_initial_census_changed"] = (
            _platform_archive_signature(platform_rows)
            != _platform_archive_signature(platform_second_rows)
        )
        if meta["platform_initial_census_changed"]:
            raise BusanYeongdoContractError(
                "lifelong consecutive complete censuses changed"
            )
        root_recheck_first, _, _ = _parse_root_page(
            final_rechecks.values[("root", "first")][0],
            requested_page=1,
            expected_last=root_last,
        )
        root_recheck_last, _, _ = _parse_root_page(
            final_rechecks.values[("root", "last")][0],
            requested_page=root_last,
            expected_last=root_last,
        )
        if _root_signature(root_recheck_first) != _root_signature(root_pages[1]) or (
            _root_signature(root_recheck_last) != _root_signature(root_pages[root_last])
        ):
            raise BusanYeongdoContractError("Yeongdo boundary page changed on recheck")
        city_recheck_first, _ = _parse_city_page(
            final_rechecks.values[("city", "first")][0],
            page=1,
            expected_last=city_last,
        )
        city_recheck_last, _ = _parse_city_page(
            final_rechecks.values[("city", "last")][0],
            page=city_last,
            expected_last=city_last,
        )
        if _city_signature(city_recheck_first) != _city_signature(city_pages[1]) or (
            _city_signature(city_recheck_last) != _city_signature(city_pages[city_last])
        ):
            raise BusanYeongdoContractError("Busan city boundary page changed on recheck")
        platform_pages = platform_second_pages
        platform_page_counts = platform_second_counts
        platform_rows = platform_second_rows
    except Exception as exc:
        meta["configured_collection_error"] = f"stability recheck: {_clean(exc)}"
        return [], BUSAN_YEONGDO_PARSER, meta

    try:
        root_by_id = {
            _clean(row.get("raw_fields", {}).get("source_identity")): row
            for row in root_rows
        }
        external_rows: list[dict[str, Any]] = []
        native_rows: list[dict[str, Any]] = []
        external_ids: list[str] = []
        for row in platform_rows:
            raw = row.get("raw_fields", {})
            kind = _clean(raw.get("identity_kind"))
            if kind == "external":
                identity = _platform_external_idx(raw.get("identity"))
                external_ids.append(identity)
                external_rows.append(row)
                if identity not in root_by_id:
                    raise BusanYeongdoContractError(
                        f"lifelong external lecIdx {identity} is absent from canonical idx"
                    )
            elif kind == "internal":
                native_rows.append(_platform_native_row(row))
            else:
                raise BusanYeongdoContractError(
                    f"lifelong Yeongdo row has unsupported identity kind {kind!r}"
                )
        native_ids = [
            _clean(row.get("raw_fields", {}).get("identity")) for row in native_rows
        ]
        if len(native_ids) != len(set(native_ids)):
            raise BusanYeongdoContractError("native lifelong identities are duplicated")
        root_eligible = [
            row
            for row in root_rows
            if row.get("raw_fields", {}).get("education_eligible")
        ]
        root_current = [row for row in root_eligible if row["end_date"] >= cutoff.isoformat()]
        native_current = [row for row in native_rows if row["end_date"] >= cutoff.isoformat()]
        city_current = [row for row in city_rows if row["end_date"] >= cutoff.isoformat()]
        current_rows = [*root_current, *native_current, *city_current]
        if len(current_rows) > detail_cap:
            raise BusanYeongdoContractError(
                f"detail_limit cap allows {detail_cap} of {len(current_rows)} current details"
            )
        if meta["required_list_requests"] + len(current_rows) > request_cap:
            raise BusanYeongdoContractError(
                f"max_requests cap allows {request_cap} of at least "
                f"{meta['required_list_requests'] + len(current_rows)} complete requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"ownership/current partition: {_clean(exc)}"
        return [], BUSAN_YEONGDO_PARSER, meta

    detail_jobs: list[tuple[Any, str, Probe]] = []
    for row in root_current:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        url = busan_yeongdo_detail_url(identity)
        detail_jobs.append(
            (
                ("root", identity),
                url,
                lambda soup, row=row, url=url: _parse_root_detail(soup, url, row),
            )
        )
    for row in native_current:
        identity = _clean(row.get("raw_fields", {}).get("identity"))
        url = busan_yeongdo_lifelong_detail_url(identity)
        detail_jobs.append(
            (
                ("platform", identity),
                url,
                lambda soup, row=row, url=url: _parse_platform_detail(soup, url, row),
            )
        )
    for row in city_current:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        url = _clean(row.get("raw_url"))
        detail_jobs.append(
            (
                ("city", identity),
                url,
                lambda soup, row=row, url=url: _parse_city_detail(soup, url, row),
            )
        )
    details = _fetch_many(
        detail_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(details, list_phase=False)
    meta["detail_attempts"] = len(detail_jobs)
    meta["detail_errors"] = len(details.errors)
    if details.errors or len(details.values) != len(detail_jobs):
        meta["configured_collection_error"] = "; ".join(details.errors) or (
            "missing one or more required current/future details"
        )
        return [], BUSAN_YEONGDO_PARSER, meta
    try:
        enriched: list[dict[str, Any]] = []
        for row in root_current:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            soup, final_url = details.values[("root", identity)]
            enriched.append(_parse_root_detail(soup, final_url, row))
        for row in native_current:
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            soup, final_url = details.values[("platform", identity)]
            enriched.append(_parse_platform_detail(soup, final_url, row))
        for row in city_current:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            soup, final_url = details.values[("city", identity)]
            enriched.append(_parse_city_detail(soup, final_url, row))
    except Exception as exc:
        meta["detail_errors"] += 1
        meta["configured_collection_error"] = f"detail contract: {_clean(exc)}"
        return [], BUSAN_YEONGDO_PARSER, meta

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
            f"dedupe changed complete row count {len(safe_rows)} to {len(result)}"
        )
        return [], BUSAN_YEONGDO_PARSER, meta

    root_exclusions = Counter(
        _clean(row.get("raw_fields", {}).get("education_exclusion_reason"))
        for row in root_rows
        if _clean(row.get("raw_fields", {}).get("education_exclusion_reason"))
    )
    status_counts = Counter(_clean(row.get("status")) for row in result)
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    root_branch_counts = Counter(_clean(row.get("branch")) for row in root_rows)
    root_current_branches = Counter(_clean(row.get("branch")) for row in root_current)
    city_branches = Counter(_clean(row.get("branch")) for row in city_rows)
    education_source_rows = len(root_eligible) + len(native_rows) + len(city_rows)
    expired_count = education_source_rows - len(current_rows)
    duplicate_source_identity_count = len(external_ids) - len(set(external_ids))
    meta.update(
        {
            "network_requests": budget.count,
            "root_source_rows": len(root_rows),
            "root_page_counts": root_page_counts,
            "root_current_count": len(root_current),
            "root_non_education_count": len(root_rows) - len(root_eligible),
            "root_exclusion_counts": dict(root_exclusions),
            "root_branch_counts": dict(root_branch_counts),
            "root_current_branch_counts": dict(root_current_branches),
            "platform_source_rows": len(platform_rows),
            "platform_page_counts": platform_page_counts,
            "platform_native_rows": len(native_rows),
            "platform_native_current_count": len(native_current),
            "platform_external_duplicate_rows": len(external_rows),
            "platform_external_unique_identities": len(set(external_ids)),
            "platform_external_repeated_rows": duplicate_source_identity_count,
            "platform_external_unmatched_rows": 0,
            "city_source_rows": len(city_rows),
            "city_page_counts": city_page_counts,
            "city_current_count": len(city_current),
            "city_branch_counts": dict(city_branches),
            "source_total": len(root_rows) + len(platform_rows) + len(city_rows),
            "source_rows": len(root_rows) + len(platform_rows) + len(city_rows),
            "unique_education_source_rows": education_source_rows,
            "current_source_count": len(current_rows),
            "expired_count": expired_count,
            "returned_count": len(result),
            "detail_pages": len(details.values),
            "detail_errors": 0,
            "application_control_count": sum(
                bool(row.get("reservation_available")) for row in result
            ),
            "status_counts": dict(status_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "duplicate_source_identity_count": duplicate_source_identity_count,
            "privacy_redactions": privacy_redactions,
            "pagination_detected": any(
                value > 1 for value in (root_last, platform_last, city_last)
            ),
            "pagination_complete": True,
            "details_complete": len(details.values) == len(current_rows),
            "snapshot_complete": True,
            "source_cap_reached": False,
            "no_current_data": not result,
            "no_current_reason": (
                "all complete Yeongdo education ledgers have ended" if not result else ""
            ),
            "configured_collection_error": "",
        }
    )
    return result, BUSAN_YEONGDO_PARSER, meta


collect = collect_busan_yeongdo_education


__all__ = [
    "BUSAN_CITY_RESIDENT_OFFICE",
    "BUSAN_CITY_YEONGDO_GUGUN",
    "BUSAN_CITY_YEONGDO_PROVIDER",
    "BUSAN_CITY_YEONGDO_URL",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_LIFELONG_YEONGDO_OFFICE",
    "BUSAN_LIFELONG_YEONGDO_PAGE_SIZE",
    "BUSAN_YEONGDO_CANONICAL_URL",
    "BUSAN_YEONGDO_CANDIDATE_DECISIONS",
    "BUSAN_YEONGDO_CANDIDATE_IDS",
    "BUSAN_YEONGDO_DISCOVERY_AUDIT",
    "BUSAN_YEONGDO_HOST",
    "BUSAN_YEONGDO_MUNICIPALITY_CODE",
    "BUSAN_YEONGDO_MUNICIPALITY_NAME",
    "BUSAN_YEONGDO_NOTICE_PROVIDER",
    "BUSAN_YEONGDO_NOTICE_URL",
    "BUSAN_YEONGDO_OWNER_BOUNDARY_AUDIT",
    "BUSAN_YEONGDO_OWNERSHIP_SCOPE",
    "BUSAN_YEONGDO_PARSER",
    "BUSAN_YEONGDO_PROVIDER",
    "BUSAN_YEONGDO_URL",
    "BusanYeongdoContractError",
    "busan_yeongdo_application_url",
    "busan_yeongdo_city_detail_url",
    "busan_yeongdo_city_list_url",
    "busan_yeongdo_detail_url",
    "busan_yeongdo_lifelong_detail_url",
    "busan_yeongdo_lifelong_list_url",
    "busan_yeongdo_list_url",
    "canonical_busan_yeongdo_course_identity",
    "collect",
    "collect_busan_yeongdo_education",
    "is_busan_yeongdo_education_target",
    "is_target",
]
