"""Atomic education collector for Busan Nam-gu's three official ledgers.

The search-discovered ``sub03/sub06_list.php`` URL is a retired, empty shell.
The current district catalogue is the unfiltered ``A0005`` general-course
ledger.  It is retained under the discovered provider identity, but every
crawl uses the current canonical URL.

Two companion ledgers are collected in the same fail-closed snapshot: the
Busan Lifelong Learning Platform office ``OFFICE_00002634`` and the Busan
integrated-reservation partition fixed to Nam-gu (4) and resident councils
(33).  Native ``LEARNING_*`` platform rows are independent courses.  External
Nam-gu URLs are suppressed only when their exact ``idx`` ownership is proved;
the one audited unpublished ``idx=3064`` test row is explicitly excluded.

Every declared page, the immediate post-final sentinel, stable first/final
rechecks, and every current/future detail are mandatory.  A failure in any
ledger discards the union.  Applicant lists/forms and attachments are never
followed.  Instructor, contact, eligibility-widget, and free-form values are
never read or persisted.
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


BUSAN_NAMGU_PROVIDER = "MUNI_WWW_BSNAMGU_GO_KR_664BF631"
BUSAN_CITY_NAMGU_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_6FF7EAF5"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_NAMGU_MUNICIPALITY_CODE = "2629000000"
BUSAN_NAMGU_MUNICIPALITY_NAME = "부산광역시 남구"
BUSAN_NAMGU_HOST = "www.bsnamgu.go.kr"
BUSAN_NAMGU_LEGACY_PATH = "/edu/sub03/sub06_list.php"
BUSAN_NAMGU_PATH = "/edu/sub/sub.php"
BUSAN_NAMGU_MENU = "A0005"
BUSAN_NAMGU_REGISTERED_URL = (
    f"https://{BUSAN_NAMGU_HOST}{BUSAN_NAMGU_LEGACY_PATH}"
)
BUSAN_NAMGU_CANONICAL_URL = (
    f"https://{BUSAN_NAMGU_HOST}{BUSAN_NAMGU_PATH}?menucd={BUSAN_NAMGU_MENU}"
)
BUSAN_NAMGU_URL = BUSAN_NAMGU_CANONICAL_URL
BUSAN_NAMGU_PAGE_SIZE = 9

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_NAMGU_GUGUN = "4"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_NAMGU_CANDIDATE_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    "resveGroupSn=&progrmSn=&srchGugun=4&srchResveInsttCd=33"
)
BUSAN_CITY_NAMGU_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_NAMGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_LIFELONG_NAMGU_OFFICE = "OFFICE_00002634"
BUSAN_LIFELONG_NAMGU_OFFICE_NAME = "남구청"
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_OFFICE_URL = _lifelong.BUSAN_LIFELONG_URL

BUSAN_NAMGU_FETCH_ATTEMPTS = 3
BUSAN_NAMGU_MAX_WORKERS = 12
BUSAN_NAMGU_MAX_HTML_BYTES = 4_000_000
BUSAN_NAMGU_PARSER = (
    "busan_namgu_a0005_complete_idx_pages+empty_sentinel+stable_first_last+"
    "lifelong_office00002634_complete_native_learning_details+exact_external_"
    "idx_ownership+busan_reserve_gugun4_office33_complete+current_details+"
    "identity_bound_application_no_applicant_fetch+pii_allowlist+atomic_three_ledger"
)
BUSAN_NAMGU_OWNERSHIP_SCOPE = (
    "busan_namgu_complete_lifelong_education_and_resident_council_courses"
)

BUSAN_NAMGU_CANDIDATE_IDS: Mapping[str, str] = {
    "legacy_district_course_shell": "MUNI_IR_44E747C4FA57",
    "busan_resident_councils": "MUNI_IR_4B0EC6133F42",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
}

BUSAN_NAMGU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_NAMGU_PROVIDER: {
        "decision": "retain_provider_retarget_retired_shell_to_complete_a0005_ledger",
        "candidate_id": BUSAN_NAMGU_CANDIDATE_IDS[
            "legacy_district_course_shell"
        ],
        "registered_url": BUSAN_NAMGU_REGISTERED_URL,
        "canonical_url": BUSAN_NAMGU_CANONICAL_URL,
        "identity_rule": "numeric idx on the canonical A0005 detail route",
    },
    BUSAN_CITY_NAMGU_PROVIDER: {
        "decision": "collect_exact_namgu_resident_council_partition_as_companion",
        "candidate_id": BUSAN_NAMGU_CANDIDATE_IDS["busan_resident_councils"],
        "candidate_url": BUSAN_CITY_NAMGU_CANDIDATE_URL,
        "canonical_url": BUSAN_CITY_NAMGU_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_NAMGU_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
        "identity_rule": "resveGroupSn plus progrmSn",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": (
            "collect_native_learning_ids; suppress exact external Nam-gu idx aliases"
        ),
        "candidate_id": BUSAN_NAMGU_CANDIDATE_IDS[
            "busan_lifelong_federation"
        ],
        "url": BUSAN_LIFELONG_OFFICE_URL,
        "office_code": BUSAN_LIFELONG_NAMGU_OFFICE,
        "identity_rule": (
            "LEARNING_* is independent; external bsnamgu idx stays in district namespace"
        ),
    },
    "OFFICIAL_NON_EDUCATION_MENUS": {
        "decision": "exclude_experience_facility_performance_and_recruitment_families",
        "reason": "only structured 강좌/교육 ledgers are in scope",
    },
    "APPLICANT_AND_ACCOUNT_BOUNDARY": {
        "decision": "never_fetch_or_persist",
        "reason": "application forms, applicant lists, login and account pages contain PII",
    },
}

BUSAN_NAMGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "registered_url": BUSAN_NAMGU_REGISTERED_URL,
    "registered_response": "HTTP 200 retired shell with zero course rows",
    "canonical_url": BUSAN_NAMGU_CANONICAL_URL,
    "canonical_rows": 1153,
    "canonical_data_pages": 129,
    "canonical_page_counts": {"1-128": 9, "129": 1},
    "canonical_sentinel_page": 130,
    "canonical_sentinel_mode": "empty",
    "canonical_unique_idx": 1153,
    "canonical_reversed_historical_rows": 2,
    "canonical_application_anomaly_rows": 9,
    "canonical_current_rows": 0,
    "lifelong_office": BUSAN_LIFELONG_NAMGU_OFFICE,
    "lifelong_rows": 153,
    "lifelong_data_pages": 2,
    "lifelong_page_counts": {"1": 100, "2": 53},
    "lifelong_sentinel_page": 3,
    "lifelong_native_rows": 152,
    "lifelong_native_current_rows": 62,
    "lifelong_current_source_status_counts": {
        "접수중": 40,
        "대기접수": 5,
        "마감": 17,
    },
    "lifelong_priority_application_rows": 35,
    "lifelong_general_application_rows": 5,
    "lifelong_waitlist_application_rows": 5,
    "lifelong_external_rows": 1,
    "lifelong_external_idx": "3064",
    "lifelong_external_visible_catalogue_matches": 0,
    "lifelong_external_unpublished_test_rows": 1,
    "resident_url": BUSAN_CITY_NAMGU_URL,
    "resident_rows": 42,
    "resident_data_pages": 5,
    "resident_page_counts": {"1-4": 10, "5": 2},
    "resident_sentinel_page": 6,
    "resident_current_rows": 41,
    "resident_undated_closed_rows": 1,
    "resident_current_branch_counts": {
        "남구 대연4동 주민자치회": 12,
        "남구 대연1동 주민자치회": 10,
        "남구 우암동 주민자치회": 12,
        "남구 문현4동 주민자치회": 7,
    },
    "current_detail_rows": 103,
    "atomic_current_rows": 103,
    "atomic_status_counts": {"OPEN": 45, "CLOSED": 58},
    "atomic_application_control_rows": 45,
    "source_rows": 1348,
    "unique_education_source_rows": 1347,
    "required_list_requests": 145,
    "complete_network_requests": 248,
    "conclusion": (
        "collect A0005, native OFFICE_00002634 rows, and exact resident-council "
        "partition; suppress owner-bound external idx aliases and the audited test"
    ),
}

BUSAN_NAMGU_PII_FIELDS_NEVER_READ = (
    "district 강사명/문의전화/접수메일/FAX번호 values",
    "district 강좌소개/유의사항/계획서/신청서/기타 values",
    "lifelong 문의전화/접수인원/강사/첨부/소개/계획/주의/검색어 values",
    "lifelong 수강료 기타/직장인 여부/강좌제한 free-form values",
    "Busan city 문의 and detail 문의전화 values",
    "attachments and free-form detail values",
    "applicant lists, application forms, login/account payloads",
)


class BusanNamguContractError(ValueError):
    """Raised when one of the audited Nam-gu source contracts changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or invalid-HTML response."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Probe = Callable[[BeautifulSoup], None]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})(?:\.)?(?!\d)"
)
_ISO_RANGE_RE = re.compile(
    r"^\s*((?:20\d{2}|0000)-\d{2}-\d{2})\s*~\s*"
    r"((?:20\d{2}|0000)-\d{2}-\d{2})\s*$"
)
_LOCAL_TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건의\s*게시물이\s*있습니다\.")
_LOCAL_CAPACITY_RE = re.compile(r"^총\s*(\d+)명$")
_LOCAL_ENROLMENT_RE = re.compile(
    r"^(\d+)\s*/\s*(\d+)명\s*\(\s*대기\s*:\s*(\d+)명\s*\)$"
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
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_LOCAL_LIST_LABELS = (
    "접수기간",
    "교육기간",
    "교육장소",
    "모집인원",
    "접수현황",
    "접수방법",
)
_LOCAL_DETAIL_SAFE_LABELS = frozenset(
    {
        "사업분류",
        "교육기간",
        "교육시간",
        "교육대상",
        "정원",
        "교육장소",
        "수강료",
        "교육방법",
        "접수기간",
        "접수방법",
    }
)
_LOCAL_DETAIL_PRIVATE_LABELS = frozenset(
    {
        "강사명",
        "문의전화",
        "접수메일",
        "FAX번호",
        "강좌소개",
        "강좌소개이미지",
        "유의사항",
        "강의계획서",
        "수강신청서",
        "기타",
    }
)
_AUDITED_REVERSED_LOCAL_RANGES: Mapping[str, str] = {
    "2574": "2024-05-13 ~ 2024-04-11",
    "2370": "2023-05-13 ~ 2023-05-01",
}
_AUDITED_LOCAL_APPLICATION_ANOMALIES: Mapping[str, str] = {
    "3057": "2026-01-06 ~ 0000-00-00",
    "2688": "2024-05-27 ~ 2024-05-26",
    "2692": "2024-05-27 ~ 2024-05-26",
    "2691": "2024-05-27 ~ 2024-05-26",
    "2690": "2024-05-27 ~ 2024-05-26",
    "2510": "2023-11-30 ~ 2023-11-29",
    "2511": "2023-11-30 ~ 2023-11-29",
    "2512": "2023-11-30 ~ 2023-11-29",
    "2516": "2023-11-30 ~ 2023-11-29",
}
_AUDITED_PLATFORM_UNPUBLISHED_TEST: Mapping[str, str] = {
    "identity": "3064",
    "title": "테스트",
    "start_date": "2026-02-19",
    "end_date": "2026-02-20",
}
_AUDITED_CITY_UNDATED_ROW: Mapping[str, str] = {
    "identity": "199:23279",
    "title": "[권역]꽃을 그리는 시간",
    "title_attribute": "꽃을 그리는 시간",
    "status": "접수마감",
    "branch": "남구 대연3동 주민자치회",
    "dates": "[신청] ~ [행사] ~",
    "method": "-",
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
_PLATFORM_DETAIL_OPTIONAL_LABELS = frozenset(
    {"수강료 기타", "직장인 여부"}
)
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
        raise BusanNamguContractError(
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
        raise BusanNamguContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanNamguContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanNamguContractError(f"{label} must be positive")
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


def is_busan_namgu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_NAMGU_PROVIDER:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(BUSAN_NAMGU_REGISTERED_URL),
        _compare_url(BUSAN_NAMGU_CANONICAL_URL),
    }


is_target = is_busan_namgu_education_target


def busan_namgu_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    return BUSAN_NAMGU_CANONICAL_URL + "&" + urlencode({"pn": value})


def busan_namgu_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanNamguContractError("invalid Nam-gu course identity")
    return f"https://{BUSAN_NAMGU_HOST}{BUSAN_NAMGU_PATH}?" + urlencode(
        (("menucd", BUSAN_NAMGU_MENU), ("idx", value), ("sort1", "info"))
    )


def busan_namgu_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", value),
            ("srchGugun", BUSAN_CITY_NAMGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_namgu_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanNamguContractError("invalid Busan city course identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def busan_namgu_lifelong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "lifelong page")
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(
        _lifelong._list_payload(BUSAN_LIFELONG_NAMGU_OFFICE, value)
    )


def busan_namgu_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanNamguContractError("invalid lifelong course identity")
    return _lifelong.busan_lifelong_detail_url(value)


_LOCAL_DETAIL_QUERY_KEYS = frozenset(
    {
        "menucd",
        "idx",
        "sort1",
        "pn",
        "se1",
        "se2",
        "se3",
        "se4",
        "se5",
        "key",
        "key_name",
        "trLectureOption",
    }
)


def canonical_busan_namgu_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_NAMGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_NAMGU_PATH
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not {"menucd", "idx", "sort1"}.issubset(query) or set(query) - _LOCAL_DETAIL_QUERY_KEYS:
        return ""
    if query.get("menucd") != [BUSAN_NAMGU_MENU] or query.get("sort1") != ["info"]:
        return ""
    for key in set(query) - {"menucd", "idx", "sort1", "pn"}:
        if query.get(key) != [""]:
            return ""
    identity = _query_one(query, "idx")
    if not _IDENTITY_RE.fullmatch(identity):
        return ""
    page = _query_one(query, "pn")
    if page and (not page.isdigit() or int(page) < 1):
        return ""
    return f"idx:{identity}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
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
                raise BusanNamguContractError(
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
        or _normal_path(final.path) != _normal_path(requested.path)
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
    size = len(content) if isinstance(content, bytes) else len(str(content).encode("utf-8"))
    if size > BUSAN_NAMGU_MAX_HTML_BYTES:
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
        for attempt in range(1, BUSAN_NAMGU_FETCH_ATTEMPTS + 1):
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
                if attempt < BUSAN_NAMGU_FETCH_ATTEMPTS:
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
            raise BusanNamguContractError("invalid source date") from exc
    return result


def _date_range(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, str]:
    found = _dates(value)
    if not found and allow_empty:
        return "", ""
    if len(found) != 2 or found[1] < found[0]:
        raise BusanNamguContractError(f"{label} changed or is reversed")
    return found[0].isoformat(), found[1].isoformat()


def _local_href_identity(value: Any, *, page: int) -> str:
    parsed = urlparse(urljoin(BUSAN_NAMGU_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_NAMGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_NAMGU_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != _LOCAL_DETAIL_QUERY_KEYS
        or query.get("menucd") != [BUSAN_NAMGU_MENU]
        or query.get("sort1") != ["info"]
        or query.get("pn") != [str(page)]
    ):
        raise BusanNamguContractError("unsafe district detail route")
    for key in _LOCAL_DETAIL_QUERY_KEYS - {"menucd", "idx", "sort1", "pn"}:
        if query.get(key) != [""]:
            raise BusanNamguContractError("filtered district detail route")
    identity = _query_one(query, "idx")
    if not _IDENTITY_RE.fullmatch(identity):
        raise BusanNamguContractError("malformed district idx")
    return identity


def _local_paging_number(value: Any) -> str:
    parsed = urlparse(urljoin(BUSAN_NAMGU_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_NAMGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_NAMGU_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"menucd", "pn"}
        or query.get("menucd") != [BUSAN_NAMGU_MENU]
    ):
        return ""
    page = _query_one(query, "pn")
    return page if page.isdigit() and int(page) >= 1 else ""


def _local_list_contract(
    soup: BeautifulSoup, *, page: int
) -> tuple[int, int, Tag]:
    if _text(_one(soup.select("title"), "district list title")) != (
        "강좌신청 > 일반강좌"
    ):
        raise BusanNamguContractError("district list title changed")
    form = _one(
        soup.select("form#boxsel2[name='boxsel2']"), "district search form"
    )
    action = urlparse(urljoin(BUSAN_NAMGU_CANONICAL_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "get"
        or (action.hostname or "").rstrip(".").lower() != BUSAN_NAMGU_HOST
        or _normal_path(action.path) != BUSAN_NAMGU_PATH
    ):
        raise BusanNamguContractError("district search form changed")
    menu = _one(form.select("input[name='menucd']"), "district menu field")
    if _clean(menu.get("value")) != BUSAN_NAMGU_MENU:
        raise BusanNamguContractError("district menu identity changed")
    total_node = _one(soup.select("p.page_num"), "district total declaration")
    total_match = _LOCAL_TOTAL_RE.fullmatch(_text(total_node))
    if not total_match:
        raise BusanNamguContractError("district total declaration changed")
    total = int(total_match.group(1).replace(",", ""))
    if total < 1:
        raise BusanNamguContractError("district catalogue unexpectedly empty")
    last = math.ceil(total / BUSAN_NAMGU_PAGE_SIZE)
    end = _one(soup.select("p.pageing > a.btn_end[href]"), "district final page")
    if _local_paging_number(end.get("href")) != str(last):
        raise BusanNamguContractError("district final-page control changed")
    active = soup.select("p.pageing > a.on")
    if page <= last:
        if len(active) != 1 or _text(active[0]) != str(page):
            raise BusanNamguContractError("district active page differs from request")
    elif page == last + 1:
        if active:
            raise BusanNamguContractError("district sentinel retained an active page")
    else:
        raise BusanNamguContractError("district request passed sentinel boundary")
    root = _one(
        soup.select("div.multiPurpose-list > ul"), "district course list"
    )
    count = len(root.find_all("li", recursive=False))
    expected = (
        BUSAN_NAMGU_PAGE_SIZE
        if page < last
        else total - BUSAN_NAMGU_PAGE_SIZE * (last - 1)
        if page == last
        else 0
    )
    if count != expected:
        raise BusanNamguContractError(
            f"district page {page} has {count} rows, expected {expected}"
        )
    return total, last, root


def _node_value_without_strong(node: Tag) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    for strong in clone.select("strong"):
        strong.extract()
    return _clean(clone.get_text(" ", strip=True))


def _local_iso_range(
    value: Any, *, identity: str, field: str
) -> tuple[str, str, bool]:
    raw = _clean(value)
    match = _ISO_RANGE_RE.fullmatch(raw)
    if not match:
        raise BusanNamguContractError(
            f"district {identity} {field} range changed"
        )
    left, right = match.groups()
    if left.startswith("0000") or right.startswith("0000"):
        if (
            field == "application"
            and _AUDITED_LOCAL_APPLICATION_ANOMALIES.get(identity) == raw
        ):
            return "", "", True
        raise BusanNamguContractError(
            f"district {identity} has an unaudited zero {field} date"
        )
    try:
        start, end = date.fromisoformat(left), date.fromisoformat(right)
    except ValueError as exc:
        raise BusanNamguContractError(
            f"district {identity} {field} contains an invalid date"
        ) from exc
    if end < start:
        if (
            field == "education"
            and _AUDITED_REVERSED_LOCAL_RANGES.get(identity) == raw
        ):
            return "", "", True
        if (
            field == "application"
            and _AUDITED_LOCAL_APPLICATION_ANOMALIES.get(identity) == raw
        ):
            return "", "", True
        raise BusanNamguContractError(
            f"district {identity} has an unaudited reversed {field} range"
        )
    return start.isoformat(), end.isoformat(), False


def _parse_local_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last, root = _local_list_contract(soup, page=page)
    if expected_total is not None and total != expected_total:
        raise BusanNamguContractError("district displayed total changed")
    if expected_last is not None and last != expected_last:
        raise BusanNamguContractError("district displayed final page changed")
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(root.find_all("li", recursive=False), 1):
        link = _one(item.find_all("a", recursive=False), "district course link")
        identity = _local_href_identity(link.get("href"), page=page)
        status_node = _one(
            link.select(":scope > span[class^='ty']"), "district status"
        )
        source_status = _text(status_node)
        if source_status not in _LOCAL_STATUS_MAP:
            raise BusanNamguContractError(
                f"district {identity} has unknown status {source_status!r}"
            )
        title = _text(
            _one(link.select(":scope > p.s-title"), "district course title")
        )
        if not title:
            raise BusanNamguContractError(f"district {identity} has no title")
        info = _one(link.select(":scope > ul.info"), "district card values")
        pairs: dict[str, str] = {}
        labels: list[str] = []
        for definition in info.find_all("li", recursive=False):
            heading = _one(
                definition.find_all("strong", recursive=False),
                "district card label",
            )
            label = _text(heading)
            if not label or label in pairs:
                raise BusanNamguContractError("duplicate district card label")
            labels.append(label)
            pairs[label] = _node_value_without_strong(definition)
        if tuple(labels) != _LOCAL_LIST_LABELS:
            raise BusanNamguContractError("district card labels changed")
        start, end, historical_date_anomaly = _local_iso_range(
            pairs["교육기간"], identity=identity, field="education"
        )
        apply_start, apply_end, historical_apply_anomaly = _local_iso_range(
            pairs["접수기간"], identity=identity, field="application"
        )
        capacity = _LOCAL_CAPACITY_RE.fullmatch(pairs["모집인원"])
        enrolment = _LOCAL_ENROLMENT_RE.fullmatch(pairs["접수현황"])
        if not capacity or not enrolment:
            raise BusanNamguContractError(
                f"district {identity} capacity contract changed"
            )
        raw_url = busan_namgu_detail_url(identity)
        rows.append(
            {
                "provider": BUSAN_NAMGU_PROVIDER,
                "provider_course_id": (
                    f"{BUSAN_NAMGU_PROVIDER}:district:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": "남구평생학습관",
                "branch_code": "namgu-lifelong-centre",
                "preserve_branch": True,
                "provider_organizer": "부산광역시 남구 평생교육과",
                "category": "일반강좌",
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _LOCAL_STATUS_MAP[source_status],
                "period": f"{start} ~ {end}" if start and end else "",
                "start_date": start,
                "end_date": end,
                "apply_period": (
                    f"{apply_start} ~ {apply_end}"
                    if apply_start and apply_end
                    else ""
                ),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "application_method_raw": pairs["접수방법"],
                "venue_name": pairs["교육장소"],
                "target": "",
                "fee": "",
                "capacity_total": int(capacity.group(1)),
                "capacity_current": int(enrolment.group(1)),
                "waitlist_current": int(enrolment.group(3)),
                "municipality_code": BUSAN_NAMGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_NAMGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "남구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_NAMGU_PARSER,
                    "source_catalog": "busan_namgu_a0005_general_courses",
                    "source_identity": identity,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_period": pairs["접수기간"],
                    "source_education_period": pairs["교육기간"],
                    "source_application_method": pairs["접수방법"],
                    "historical_date_anomaly": historical_date_anomaly,
                    "historical_application_anomaly": historical_apply_anomaly,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    return rows, total, last


def _local_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        repr(
            [
                (
                    _clean(row.get("provider_course_id")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(row.get("raw_fields", {}).get("source_education_period")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _local_detail_values(soup: BeautifulSoup) -> tuple[dict[str, str], set[str]]:
    table = _one(soup.select("div.tbl-wrap > table.tbl"), "district detail table")
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    labels: list[str] = []
    for row in table.select(":scope > tbody > tr"):
        children = row.find_all(["th", "td"], recursive=False)
        index = 0
        while index < len(children):
            heading = children[index]
            if heading.name != "th" or index + 1 >= len(children):
                raise BusanNamguContractError("district detail table shape changed")
            value = children[index + 1]
            if value.name != "td":
                raise BusanNamguContractError("district detail value shape changed")
            label = _text(heading)
            if not label or label in labels:
                raise BusanNamguContractError("duplicate district detail label")
            labels.append(label)
            if label in _LOCAL_DETAIL_SAFE_LABELS:
                safe[label] = _text(value)
            elif label in _LOCAL_DETAIL_PRIVATE_LABELS:
                skipped.add(label)
            else:
                raise BusanNamguContractError(
                    f"unknown district detail field {label!r}"
                )
            index += 2
    if not _LOCAL_DETAIL_SAFE_LABELS.issubset(safe):
        raise BusanNamguContractError("district safe detail fields changed")
    if not {
        "강사명",
        "문의전화",
        "접수메일",
        "FAX번호",
        "강좌소개",
    }.issubset(skipped):
        raise BusanNamguContractError("district private detail boundary changed")
    return safe, skipped


def _local_detail_application_control(
    soup: BeautifulSoup, *, identity: str
) -> tuple[bool, str]:
    controls = []
    for anchor in soup.select("div.taC.mT30 > a[href]"):
        label = _text(anchor)
        if label in {"목록", "취소", "뒤로"}:
            continue
        controls.append(anchor)
    if len(controls) > 1:
        raise BusanNamguContractError("multiple district application controls")
    if not controls:
        return False, ""
    control = controls[0]
    label = _text(control)
    parsed = urlparse(urljoin(BUSAN_NAMGU_CANONICAL_URL, _clean(control.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        not any(token in label for token in ("신청", "접수"))
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_NAMGU_HOST
        or _normal_path(parsed.path) != BUSAN_NAMGU_PATH
        or query.get("menucd") != [BUSAN_NAMGU_MENU]
        or query.get("idx") != [identity]
    ):
        raise BusanNamguContractError("district application identity changed")
    return True, label


def _parse_local_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    if canonical_busan_namgu_course_identity(final_url) != f"idx:{identity}":
        raise BusanNamguContractError("district detail response identity changed")
    title = _text(_one(soup.select("title"), "district detail title"))
    if title != f"강좌신청 > 일반강좌 > {_clean(parent.get('title'))}":
        raise BusanNamguContractError("district detail browser title changed")
    header = _one(soup.select("div.edu_tit"), "district detail heading")
    detail_title = _text(_one(header.select(":scope > div.subject"), "detail title"))
    detail_status = _text(
        _one(header.select(":scope div[class^='sang_type'] > span"), "detail status")
    )
    if detail_title != _clean(parent.get("title")):
        raise BusanNamguContractError("district list/detail title mismatch")
    if detail_status != _clean(raw.get("source_status")):
        raise BusanNamguContractError("district list/detail status mismatch")
    safe, skipped = _local_detail_values(soup)
    start, end = _date_range(safe["교육기간"], "district detail education period")
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
        raise BusanNamguContractError("district list/detail dates mismatch")
    if safe["교육장소"] != _clean(parent.get("venue_name")):
        raise BusanNamguContractError("district list/detail venue mismatch")
    if safe["접수방법"] != _clean(raw.get("source_application_method")):
        raise BusanNamguContractError("district list/detail method mismatch")
    has_control, control_label = _local_detail_application_control(
        soup, identity=identity
    )
    normalized_status = _LOCAL_STATUS_MAP[detail_status]
    method = safe["접수방법"]
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if not has_control:
                raise BusanNamguContractError(
                    "open district online course lacks an identity-bound control"
                )
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("직접", "방문", "전화", "이메일", "FAX")):
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanNamguContractError("open district application method changed")
    elif has_control:
        raise BusanNamguContractError("non-open district course became actionable")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "target": safe["교육대상"],
            "fee": safe["수강료"],
            "schedule_raw": safe["교육시간"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": control_label,
        "instructor_value_never_read": "강사명" in skipped,
        "contact_values_never_read": {
            "문의전화",
            "접수메일",
            "FAX번호",
        }.issubset(skipped),
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_NAMGU_OFFICE
    )
    if (
        office is None
        or office.name != BUSAN_LIFELONG_NAMGU_OFFICE_NAME
        or office.municipality_code
        or office.municipality_name
        or office.ownership != "duplicate_dedicated_namgu_owner"
    ):
        raise BusanNamguContractError("lifelong Nam-gu office ownership changed")
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
        raise BusanNamguContractError("; ".join(form_errors))
    last, last_errors = _lifelong._advertised_last(soup)
    if last_errors:
        raise BusanNamguContractError("; ".join(last_errors))
    if expected_last is not None and last != expected_last:
        raise BusanNamguContractError("lifelong displayed final page changed")
    rows, row_errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if row_errors:
        raise BusanNamguContractError("; ".join(row_errors))
    return rows, last


def _platform_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    return _lifelong._page_signature(rows)


def _platform_external_idx(value: Any) -> str:
    identity = canonical_busan_namgu_course_identity(value)
    if not identity.startswith("idx:"):
        raise BusanNamguContractError(
            "lifelong external row left the canonical Nam-gu detail scope"
        )
    return identity.removeprefix("idx:")


def _platform_unpublished_test_matches(row: Mapping[str, Any], identity: str) -> bool:
    expected = _AUDITED_PLATFORM_UNPUBLISHED_TEST
    return bool(
        identity == expected["identity"]
        and _clean(row.get("title")) == expected["title"]
        and _clean(row.get("start_date")) == expected["start_date"]
        and _clean(row.get("end_date")) == expected["end_date"]
        and _clean(row.get("raw_fields", {}).get("source_status")) == "마감"
    )


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(
        identity
    ):
        raise BusanNamguContractError("invalid native lifelong identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_NAMGU_PROVIDER,
            "provider_course_id": (
                f"{BUSAN_NAMGU_PROVIDER}:lifelong:{identity}"
            ),
            "prefer_incoming_provider_course_id": True,
            "branch": BUSAN_LIFELONG_NAMGU_OFFICE_NAME,
            "branch_code": "namgu-lifelong-office00002634",
            "preserve_branch": True,
            "provider_organizer": BUSAN_LIFELONG_NAMGU_OFFICE_NAME,
            "municipality_code": BUSAN_NAMGU_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_NAMGU_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "남구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": (
                "complete_shared_office_pages+native_current_detail"
            ),
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_NAMGU_PARSER,
        "source_catalog": "busan_lifelong_namgu_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
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
            raise BusanNamguContractError(
                f"unknown or duplicate lifelong detail field {label!r}"
            )
        labels.append(label)
        if label in _PLATFORM_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = list(_PLATFORM_DETAIL_REQUIRED_LABELS)
    without_optional = [
        label for label in labels if label not in _PLATFORM_DETAIL_OPTIONAL_LABELS
    ]
    if without_optional != required:
        raise BusanNamguContractError("lifelong detail field order changed")
    expected_skipped = (
        set(required)
        | (set(labels) & set(_PLATFORM_DETAIL_OPTIONAL_LABELS))
    ) - set(_PLATFORM_DETAIL_SAFE_LABELS)
    if skipped != expected_skipped:
        raise BusanNamguContractError("lifelong private/free detail boundary changed")
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
        raise BusanNamguContractError(
            f"lifelong detail {identity} response scope changed"
        )
    form = _one(
        soup.select("form#learningVO[name='learningVO']"),
        "lifelong detail form",
    )
    action = urlparse(urljoin(final_url, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != BUSAN_LIFELONG_DETAIL_PATH
        or parse_qs(action.query, keep_blank_values=True).get("lng_id")
        != [identity]
    ):
        raise BusanNamguContractError("lifelong detail form changed")
    identity_fields = {
        _clean(node.get("value")) for node in form.select("input[name='lng_id']")
    }
    office_fields = {
        _clean(node.get("value")) for node in form.select("input[name='inst_id']")
    }
    if identity_fields != {identity} or office_fields != {
        BUSAN_LIFELONG_NAMGU_OFFICE
    }:
        raise BusanNamguContractError(
            f"lifelong detail {identity} identity/office mismatch"
        )
    heading = _one(soup.select("h2.enrolTit"), "lifelong detail heading")
    prefix = _one(heading.select(":scope > span"), "lifelong office prefix")
    if _text(prefix) != f"[{BUSAN_LIFELONG_NAMGU_OFFICE_NAME}]":
        raise BusanNamguContractError("lifelong detail office prefix changed")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")):
        raise BusanNamguContractError(
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
        raise BusanNamguContractError(
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
            raise BusanNamguContractError(
                f"lifelong detail {identity} application dates mismatch"
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
            not in {
                "우선모집신청",
                "일반모집신청",
                "수강신청",
                "대기자신청",
            }
            or _clean(control.get("onclick"))
            != "fn_learning_apply(); return false;"
        ):
            raise BusanNamguContractError(
                f"lifelong detail {identity} application control changed"
            )
        if source_status == "대기접수" and control_label != "대기자신청":
            raise BusanNamguContractError(
                f"lifelong detail {identity} waitlist control changed"
            )
        application_type = (
            "WAITLIST_APPLY"
            if control_label == "대기자신청"
            else "ONLINE_RESERVATION"
        )
    elif controls:
        raise BusanNamguContractError(
            f"closed lifelong detail {identity} became actionable"
        )
    result = dict(parent)
    result.update(
        {
            "status": "OPEN" if active else "CLOSED",
            "application_url": (
                busan_namgu_lifelong_detail_url(identity) if active else ""
            ),
            "application_type": application_type,
            "reservation_available": active,
            "target": safe.get("교육대상", ""),
            "venue_name": (
                safe.get("교육장소") or BUSAN_LIFELONG_NAMGU_OFFICE_NAME
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
        "workplace_eligibility_value_never_read": "직장인 여부" in _labels,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, *, page: int
) -> tuple[int, Optional[Tag]]:
    if _text(_one(soup.select("title"), "Busan city list title")) != _CITY_LIST_TITLE:
        raise BusanNamguContractError("Busan city list title changed")
    form = _one(
        soup.select("form#srchForm[name='srchForm']"), "city search form"
    )
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanNamguContractError("Busan city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "city curPage field")
    if _clean(page_field.get("value")) != str(page):
        raise BusanNamguContractError("Busan city form page differs from request")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_NAMGU_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanNamguContractError(f"Busan city {name} owner filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "city last page")
    parsed = urlparse(urljoin(BUSAN_CITY_NAMGU_URL, _clean(end_link.get("href"))))
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
        or query.get("srchGugun") != [BUSAN_CITY_NAMGU_GUGUN]
        or query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
    ):
        raise BusanNamguContractError("unsafe Busan city final-page control")
    last_raw = _query_one(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanNamguContractError("invalid Busan city final page")
    last = int(last_raw)
    roots = soup.select("ul.reserveList")
    if page <= last:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last + 1:
        if roots:
            raise BusanNamguContractError(
                "Busan city sentinel unexpectedly retained a reserve list"
            )
        root = None
    else:
        raise BusanNamguContractError("Busan city request passed sentinel boundary")
    return last, root


def _city_card_date_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanNamguContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (
            date.fromisoformat(part) for part in match.groups()
        )
    except ValueError as exc:
        raise BusanNamguContractError(f"{label} contains an invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanNamguContractError(f"{label} is reversed")
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
        raise BusanNamguContractError("Busan city displayed final page changed")
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"),
            "Busan city course link",
        )
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanNamguContractError(
                f"Busan city page {page} row {position}: identity action changed"
            )
        group_id, program_id = action.groups()
        identity = f"{group_id}:{program_id}"
        title_node = _one(link.select(":scope .tit"), "Busan city course title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        audited_title_exception = _AUDITED_CITY_UNDATED_ROW
        if not title or (
            title_attribute != title
            and not (
                identity == audited_title_exception["identity"]
                and title == audited_title_exception["title"]
                and title_attribute == audited_title_exception["title_attribute"]
            )
        ):
            raise BusanNamguContractError("Busan city card title changed")
        source_status = _text(
            _one(link.select(":scope .statusMark"), "Busan city status")
        )
        if source_status not in _CITY_STATUS_MAP:
            raise BusanNamguContractError("unknown Busan city source status")
        definitions = _one(
            link.select(":scope .infoBox > dl"), "Busan city card values"
        )
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanNamguContractError("Busan city card labels changed")
        # 문의 is intentionally the final pair and its value is never read.
        safe = {
            label: _text(value)
            for label, value in zip(labels[:-1], values[:-1])
        }
        if any(not value for value in safe.values()):
            raise BusanNamguContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if not branch.startswith("남구 ") or not branch.endswith(" 주민자치회"):
            raise BusanNamguContractError("Busan city course left Nam-gu owner")
        undated = False
        try:
            apply_start, apply_end, start, end = _city_card_date_ranges(
                safe["일자"], f"Busan city page {page} row {position} dates"
            )
        except BusanNamguContractError:
            expected = _AUDITED_CITY_UNDATED_ROW
            if not (
                identity == expected["identity"]
                and title == expected["title"]
                and source_status == expected["status"]
                and branch == expected["branch"]
                and safe["일자"] == expected["dates"]
                and safe["방법"] == expected["method"]
            ):
                raise
            apply_start = apply_end = start = end = ""
            undated = True
        raw_url = busan_namgu_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_NAMGU_PROVIDER,
                "provider_course_id": (
                    f"{BUSAN_NAMGU_PROVIDER}:reserve:{group_id}:{program_id}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"namgu-reserve-{group_id}",
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
                "period": f"{start} ~ {end}" if start and end else "",
                "start_date": start,
                "end_date": end,
                "apply_period": (
                    f"{apply_start} ~ {apply_end}"
                    if apply_start and apply_end
                    else ""
                ),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": "",
                "target": safe["대상"],
                "capacity_total": None,
                "capacity_current": None,
                "venue_name": safe["장소"],
                "provider_organizer": branch,
                "municipality_code": BUSAN_NAMGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_NAMGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "남구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_NAMGU_PARSER,
                    "source_catalog": "busan_reserve_namgu_resident_councils",
                    "source_identity": identity,
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": safe["방법"],
                    "source_card_dates": safe["일자"],
                    "audited_undated_closed_row": undated,
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
                    _clean(row.get("raw_fields", {}).get("source_card_dates")),
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _city_detail_dates(value: Any, label: str) -> tuple[str, str]:
    found = _CITY_DETAIL_DATE_RE.findall(_clean(value))
    if len(found) != 2:
        raise BusanNamguContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(part) for part in found)
    except ValueError as exc:
        raise BusanNamguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BusanNamguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _safe_city_detail_values(info: Tag) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(
            definition.find_all("dt", recursive=False),
            "Busan city detail label",
        )
        value = _one(
            definition.find_all("dd", recursive=False),
            "Busan city detail value",
        )
        label = _text(heading)
        if label in labels:
            raise BusanNamguContractError("duplicate Busan city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _CITY_DETAIL_SKIPPED_LABELS:
            skipped.add(label)
        else:
            raise BusanNamguContractError(
                f"unknown Busan city detail field {label!r}"
            )
    without_attachment = [label for label in labels if label != "첨부파일"]
    if tuple(without_attachment) != _CITY_DETAIL_REQUIRED_LABELS:
        raise BusanNamguContractError("Busan city detail field order changed")
    if "문의전화" not in skipped:
        raise BusanNamguContractError("Busan city inquiry boundary changed")
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
        raise BusanNamguContractError("Busan city detail response scope changed")
    if _text(_one(soup.select("title"), "Busan city detail title")) != _CITY_LIST_TITLE:
        raise BusanNamguContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanNamguContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanNamguContractError("Busan city detail identity changed")
    heading = _one(
        form.select(":scope > div.contHeader > h3.titPage"),
        "city detail heading",
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
        raise BusanNamguContractError("Busan city list/detail title mismatch")
    if source_status != _clean(raw.get("source_status")):
        raise BusanNamguContractError("Busan city list/detail status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    _labels, safe, skipped = _safe_city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanNamguContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanNamguContractError("Busan city free-form boundary changed")
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
        raise BusanNamguContractError("Busan city list/detail dates mismatch")
    for label, key in (
        ("신청방법", "source_application_method"),
        ("운영기관", "branch"),
        ("대상", "target"),
    ):
        expected = raw.get(key) if key == "source_application_method" else parent.get(key)
        if _clean(safe[label]) != _clean(expected):
            raise BusanNamguContractError(
                f"Busan city list/detail {label} mismatch"
            )
    controls = form.select(
        ":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL"
    )
    normalized_status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    control_label = _text(controls[0]) if len(controls) == 1 else ""
    if len(controls) > 1:
        raise BusanNamguContractError("multiple Busan city application controls")
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if len(controls) != 1 or not any(
                token in control_label for token in ("신청", "예약")
            ):
                raise BusanNamguContractError(
                    "open Busan city course lacks active identity-bound control"
                )
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("방문", "전화")):
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanNamguContractError("unknown Busan city application method")
    elif normalized_status == "CLOSED":
        if control_label not in {"", "접수마감"}:
            raise BusanNamguContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED":
        if control_label not in {"", "대기중", "접수대기"}:
            raise BusanNamguContractError("scheduled Busan city control changed")
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


def _scrub_text(value: Any) -> tuple[str, int]:
    text = str(value or "")
    updated, phones = _PHONE_RE.subn("", text)
    updated, emails = _EMAIL_RE.subn("", updated)
    return _clean(updated), phones + emails


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
            updated, count = _scrub_text(value)
            redactions += count
            return updated
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
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "local_source_rows": 0,
        "local_data_pages": 0,
        "local_page_counts": {},
        "local_current_count": 0,
        "local_historical_reversed_count": 0,
        "local_historical_application_anomaly_count": 0,
        "platform_source_rows": 0,
        "platform_data_pages": 0,
        "platform_page_counts": {},
        "platform_native_rows": 0,
        "platform_native_current_count": 0,
        "platform_external_owner_identity_rows": 0,
        "platform_external_visible_duplicate_rows": 0,
        "platform_external_unpublished_test_rows": 0,
        "platform_external_unmatched_rows": 0,
        "city_source_rows": 0,
        "city_data_pages": 0,
        "city_page_counts": {},
        "city_current_count": 0,
        "city_undated_closed_count": 0,
        "city_branch_counts": {},
        "city_current_branch_counts": {},
        "source_total": 0,
        "source_rows": 0,
        "unique_education_source_rows": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "non_current_count": 0,
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
        "municipality_code": BUSAN_NAMGU_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_NAMGU_MUNICIPALITY_NAME,
        "registered_url": BUSAN_NAMGU_REGISTERED_URL,
        "canonical_url": BUSAN_NAMGU_CANONICAL_URL,
        "city_canonical_url": BUSAN_CITY_NAMGU_URL,
        "lifelong_office_code": BUSAN_LIFELONG_NAMGU_OFFICE,
        "ownership_scope": BUSAN_NAMGU_OWNERSHIP_SCOPE,
        "candidate_ids": dict(BUSAN_NAMGU_CANDIDATE_IDS),
        "owner_boundary_audit": dict(BUSAN_NAMGU_OWNER_BOUNDARY_AUDIT),
    }


def collect_busan_namgu_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 180,
    detail_limit: int = 180,
    max_requests: int = 340,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_NAMGU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future snapshot of all three ledgers."""

    meta = _base_meta()
    if not is_busan_namgu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact registered/canonical Busan Nam-gu "
            "education owner"
        )
        return [], BUSAN_NAMGU_PARSER, meta
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
        workers = min(max(1, int(max_workers)), BUSAN_NAMGU_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_NAMGU_PARSER, meta
    if page_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"max_pages cap allows {page_cap} of at least 3 ledger data pages"
        )
        return [], BUSAN_NAMGU_PARSER, meta
    if request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"max_requests cap allows {request_cap} of 3 first-ledger requests"
        )
        return [], BUSAN_NAMGU_PARSER, meta

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

    first = _fetch_many(
        (
            (
                "local",
                busan_namgu_list_url(1),
                lambda soup: _parse_local_page(soup, page=1),
            ),
            (
                "platform",
                busan_namgu_lifelong_list_url(1),
                lambda soup: _parse_platform_page(soup, page=1),
            ),
            (
                "city",
                busan_namgu_city_list_url(1),
                lambda soup: _parse_city_page(soup, page=1),
            ),
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=min(3, workers),
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(first, list_phase=True)
    if first.errors or set(first.values) != {"local", "platform", "city"}:
        meta["configured_collection_error"] = "; ".join(first.errors) or (
            "missing one or more first-ledger responses"
        )
        return [], BUSAN_NAMGU_PARSER, meta

    try:
        local_first_rows, local_total, local_last = _parse_local_page(
            first.values["local"][0], page=1
        )
        platform_first_rows, platform_last = _parse_platform_page(
            first.values["platform"][0], page=1
        )
        city_first_rows, city_last = _parse_city_page(
            first.values["city"][0], page=1
        )
        if not platform_first_rows:
            raise BusanNamguContractError(
                "lifelong Nam-gu office unexpectedly has no archive rows"
            )
        platform_total = int(
            platform_first_rows[0].get("raw_fields", {}).get("list_sequence") or 0
        )
        if (
            platform_total < 1
            or platform_last
            != math.ceil(platform_total / _lifelong.BUSAN_LIFELONG_PAGE_SIZE)
        ):
            raise BusanNamguContractError(
                "lifelong sequence total/final-page declaration is inconsistent"
            )
        data_page_count = local_last + platform_last + city_last
        required_list_requests = data_page_count + 3 + 6
        meta.update(
            {
                "local_data_pages": local_last,
                "platform_data_pages": platform_last,
                "city_data_pages": city_last,
                "required_list_requests": required_list_requests,
            }
        )
        if data_page_count > page_cap:
            raise BusanNamguContractError(
                f"max_pages cap allows {page_cap} of {data_page_count} declared data pages"
            )
        if required_list_requests > request_cap:
            raise BusanNamguContractError(
                f"max_requests cap allows {request_cap} of at least "
                f"{required_list_requests} required list requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"first-page contract: {_clean(exc)}"
        return [], BUSAN_NAMGU_PARSER, meta

    jobs: list[tuple[Any, str, Probe]] = []
    for page in range(2, local_last + 1):
        jobs.append(
            (
                ("local", page),
                busan_namgu_list_url(page),
                lambda soup, page=page: _parse_local_page(
                    soup,
                    page=page,
                    expected_total=local_total,
                    expected_last=local_last,
                ),
            )
        )
    jobs.append(
        (
            ("local", local_last + 1),
            busan_namgu_list_url(local_last + 1),
            lambda soup: _parse_local_page(
                soup,
                page=local_last + 1,
                expected_total=local_total,
                expected_last=local_last,
            ),
        )
    )
    for page in range(2, platform_last + 1):
        jobs.append(
            (
                ("platform", page),
                busan_namgu_lifelong_list_url(page),
                lambda soup, page=page: _parse_platform_page(
                    soup, page=page, expected_last=platform_last
                ),
            )
        )
    jobs.append(
        (
            ("platform", platform_last + 1),
            busan_namgu_lifelong_list_url(platform_last + 1),
            lambda soup: _parse_platform_page(
                soup, page=platform_last + 1, expected_last=platform_last
            ),
        )
    )
    for page in range(2, city_last + 1):
        jobs.append(
            (
                ("city", page),
                busan_namgu_city_list_url(page),
                lambda soup, page=page: _parse_city_page(
                    soup, page=page, expected_last=city_last
                ),
            )
        )
    jobs.append(
        (
            ("city", city_last + 1),
            busan_namgu_city_list_url(city_last + 1),
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
            ("local", local_last + 1),
            ("platform", platform_last + 1),
            ("city", city_last + 1),
        )
    )
    if remaining.errors or len(remaining.values) != len(jobs):
        meta["configured_collection_error"] = "; ".join(remaining.errors) or (
            "missing complete archive/sentinel response"
        )
        return [], BUSAN_NAMGU_PARSER, meta

    try:
        local_pages: dict[int, list[dict[str, Any]]] = {1: local_first_rows}
        local_page_counts: dict[int, int] = {1: len(local_first_rows)}
        for page in range(2, local_last + 1):
            rows, declared_total, declared_last = _parse_local_page(
                remaining.values[("local", page)][0],
                page=page,
                expected_total=local_total,
                expected_last=local_last,
            )
            if declared_total != local_total or declared_last != local_last:
                raise BusanNamguContractError("district declaration changed by page")
            local_pages[page] = rows
            local_page_counts[page] = len(rows)
        local_sentinel, sentinel_total, sentinel_last = _parse_local_page(
            remaining.values[("local", local_last + 1)][0],
            page=local_last + 1,
            expected_total=local_total,
            expected_last=local_last,
        )
        if local_sentinel or sentinel_total != local_total or sentinel_last != local_last:
            raise BusanNamguContractError(
                "district immediate post-final sentinel is not empty"
            )
        local_rows = [
            row
            for page in range(1, local_last + 1)
            for row in local_pages[page]
        ]
        if len(local_rows) != local_total:
            raise BusanNamguContractError("district parsed row count differs from total")
        local_ids = [
            _clean(row.get("raw_fields", {}).get("source_identity"))
            for row in local_rows
        ]
        if len(local_ids) != len(set(local_ids)):
            raise BusanNamguContractError("district catalogue contains duplicate idx")
        local_signatures = [
            _local_signature(local_pages[page]) for page in local_pages
        ]
        if len(local_signatures) != len(set(local_signatures)):
            raise BusanNamguContractError("district catalogue repeated a data page")

        platform_pages: dict[int, list[dict[str, Any]]] = {
            1: platform_first_rows
        }
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
            raise BusanNamguContractError(
                "lifelong immediate post-final sentinel is not empty"
            )
        platform_rows = [
            row
            for page in range(1, platform_last + 1)
            for row in platform_pages[page]
        ]
        if len(platform_rows) != platform_total:
            raise BusanNamguContractError(
                "lifelong parsed row count differs from source sequence total"
            )
        sequences = [
            int(row.get("raw_fields", {}).get("list_sequence") or 0)
            for row in platform_rows
        ]
        if sequences != list(range(platform_total, 0, -1)):
            raise BusanNamguContractError("lifelong source sequence has a gap/reorder")
        platform_signatures = [
            _platform_signature(platform_pages[page]) for page in platform_pages
        ]
        if len(platform_signatures) != len(set(platform_signatures)):
            raise BusanNamguContractError("lifelong archive repeated a data page")

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
            raise BusanNamguContractError(
                "Busan city immediate post-final sentinel is not empty"
            )
        city_rows = [
            row
            for page in range(1, city_last + 1)
            for row in city_pages[page]
        ]
        city_ids = [
            _clean(row.get("raw_fields", {}).get("source_identity"))
            for row in city_rows
        ]
        if len(city_ids) != len(set(city_ids)):
            raise BusanNamguContractError("Busan city source identities are duplicated")
        city_signatures = [_city_signature(city_pages[page]) for page in city_pages]
        if len(city_signatures) != len(set(city_signatures)):
            raise BusanNamguContractError("Busan city archive repeated a data page")
    except Exception as exc:
        meta["configured_collection_error"] = f"complete archive: {_clean(exc)}"
        return [], BUSAN_NAMGU_PARSER, meta

    recheck_jobs: list[tuple[Any, str, Probe]] = [
        (
            ("local", "first"),
            busan_namgu_list_url(1),
            lambda soup: _parse_local_page(
                soup,
                page=1,
                expected_total=local_total,
                expected_last=local_last,
            ),
        ),
        (
            ("local", "last"),
            busan_namgu_list_url(local_last),
            lambda soup: _parse_local_page(
                soup,
                page=local_last,
                expected_total=local_total,
                expected_last=local_last,
            ),
        ),
        (
            ("platform", "first"),
            busan_namgu_lifelong_list_url(1),
            lambda soup: _parse_platform_page(
                soup, page=1, expected_last=platform_last
            ),
        ),
        (
            ("platform", "last"),
            busan_namgu_lifelong_list_url(platform_last),
            lambda soup: _parse_platform_page(
                soup, page=platform_last, expected_last=platform_last
            ),
        ),
        (
            ("city", "first"),
            busan_namgu_city_list_url(1),
            lambda soup: _parse_city_page(soup, page=1, expected_last=city_last),
        ),
        (
            ("city", "last"),
            busan_namgu_city_list_url(city_last),
            lambda soup: _parse_city_page(
                soup, page=city_last, expected_last=city_last
            ),
        ),
    ]
    rechecks = _fetch_many(
        recheck_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=min(workers, 6),
        sleeper=sleeper,
        budget=budget,
    )
    record_fetch(rechecks, list_phase=True)
    meta["stability_rechecks"] = len(rechecks.values)
    if rechecks.errors or len(rechecks.values) != 6:
        meta["configured_collection_error"] = "; ".join(rechecks.errors) or (
            "missing first/final stability recheck"
        )
        return [], BUSAN_NAMGU_PARSER, meta
    try:
        local_recheck_first, _, _ = _parse_local_page(
            rechecks.values[("local", "first")][0],
            page=1,
            expected_total=local_total,
            expected_last=local_last,
        )
        local_recheck_last, _, _ = _parse_local_page(
            rechecks.values[("local", "last")][0],
            page=local_last,
            expected_total=local_total,
            expected_last=local_last,
        )
        if _local_signature(local_recheck_first) != _local_signature(
            local_pages[1]
        ) or _local_signature(local_recheck_last) != _local_signature(
            local_pages[local_last]
        ):
            raise BusanNamguContractError("district boundary page changed on recheck")
        platform_recheck_first, _ = _parse_platform_page(
            rechecks.values[("platform", "first")][0],
            page=1,
            expected_last=platform_last,
        )
        platform_recheck_last, _ = _parse_platform_page(
            rechecks.values[("platform", "last")][0],
            page=platform_last,
            expected_last=platform_last,
        )
        if _platform_signature(platform_recheck_first) != _platform_signature(
            platform_pages[1]
        ) or _platform_signature(platform_recheck_last) != _platform_signature(
            platform_pages[platform_last]
        ):
            raise BusanNamguContractError("lifelong boundary page changed on recheck")
        city_recheck_first, _ = _parse_city_page(
            rechecks.values[("city", "first")][0],
            page=1,
            expected_last=city_last,
        )
        city_recheck_last, _ = _parse_city_page(
            rechecks.values[("city", "last")][0],
            page=city_last,
            expected_last=city_last,
        )
        if _city_signature(city_recheck_first) != _city_signature(
            city_pages[1]
        ) or _city_signature(city_recheck_last) != _city_signature(
            city_pages[city_last]
        ):
            raise BusanNamguContractError("Busan city boundary page changed on recheck")
    except Exception as exc:
        meta["configured_collection_error"] = f"stability recheck: {_clean(exc)}"
        return [], BUSAN_NAMGU_PARSER, meta

    try:
        local_by_id = {
            _clean(row.get("raw_fields", {}).get("source_identity")): row
            for row in local_rows
        }
        platform_external_rows: list[dict[str, Any]] = []
        platform_visible_duplicates: list[dict[str, Any]] = []
        platform_unpublished_tests: list[dict[str, Any]] = []
        platform_native_rows: list[dict[str, Any]] = []
        external_ids: list[str] = []
        for row in platform_rows:
            raw = row.get("raw_fields", {})
            kind = _clean(raw.get("identity_kind"))
            if kind == "external":
                identity = _platform_external_idx(raw.get("identity"))
                external_ids.append(identity)
                platform_external_rows.append(row)
                if identity in local_by_id:
                    platform_visible_duplicates.append(row)
                elif _platform_unpublished_test_matches(row, identity):
                    platform_unpublished_tests.append(row)
                else:
                    raise BusanNamguContractError(
                        f"lifelong external idx {identity} is absent from canonical "
                        "catalogue and is not the audited unpublished test"
                    )
            elif kind == "internal":
                platform_native_rows.append(_platform_native_row(row))
            else:
                raise BusanNamguContractError(
                    f"lifelong Nam-gu row has unsupported identity kind {kind!r}"
                )
        native_ids = [
            _clean(row.get("raw_fields", {}).get("identity"))
            for row in platform_native_rows
        ]
        if len(native_ids) != len(set(native_ids)):
            raise BusanNamguContractError("native lifelong identities are duplicated")

        cutoff_iso = cutoff.isoformat()
        local_current = [
            row
            for row in local_rows
            if _clean(row.get("end_date"))
            and _clean(row.get("end_date")) >= cutoff_iso
        ]
        for row in local_current:
            if not row.get("apply_start") or not row.get("apply_end"):
                raise BusanNamguContractError(
                    "current district row lacks a valid application period"
                )
        platform_current = [
            row
            for row in platform_native_rows
            if _clean(row.get("end_date")) >= cutoff_iso
        ]
        city_current = [
            row
            for row in city_rows
            if _clean(row.get("end_date"))
            and _clean(row.get("end_date")) >= cutoff_iso
        ]
        current_rows = [*local_current, *platform_current, *city_current]
        if len(current_rows) > detail_cap:
            raise BusanNamguContractError(
                f"detail_limit cap allows {detail_cap} of {len(current_rows)} current details"
            )
        if meta["required_list_requests"] + len(current_rows) > request_cap:
            raise BusanNamguContractError(
                f"max_requests cap allows {request_cap} of at least "
                f"{meta['required_list_requests'] + len(current_rows)} complete requests"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = (
            f"ownership/current partition: {_clean(exc)}"
        )
        return [], BUSAN_NAMGU_PARSER, meta

    detail_jobs: list[tuple[Any, str, Probe]] = []
    for row in local_current:
        identity = _clean(row.get("raw_fields", {}).get("source_identity"))
        url = busan_namgu_detail_url(identity)
        detail_jobs.append(
            (
                ("local", identity),
                url,
                lambda soup, row=row, url=url: _parse_local_detail(soup, url, row),
            )
        )
    for row in platform_current:
        identity = _clean(row.get("raw_fields", {}).get("identity"))
        url = busan_namgu_lifelong_detail_url(identity)
        detail_jobs.append(
            (
                ("platform", identity),
                url,
                lambda soup, row=row, url=url: _parse_platform_detail(
                    soup, url, row
                ),
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
        return [], BUSAN_NAMGU_PARSER, meta
    try:
        enriched: list[dict[str, Any]] = []
        for row in local_current:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            soup, final_url = details.values[("local", identity)]
            enriched.append(_parse_local_detail(soup, final_url, row))
        for row in platform_current:
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
        return [], BUSAN_NAMGU_PARSER, meta

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
        return [], BUSAN_NAMGU_PARSER, meta

    local_reversed_count = sum(
        bool(row.get("raw_fields", {}).get("historical_date_anomaly"))
        for row in local_rows
    )
    local_apply_anomaly_count = sum(
        bool(row.get("raw_fields", {}).get("historical_application_anomaly"))
        for row in local_rows
    )
    city_undated_count = sum(
        bool(row.get("raw_fields", {}).get("audited_undated_closed_row"))
        for row in city_rows
    )
    status_counts = Counter(_clean(row.get("status")) for row in result)
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    city_branches = Counter(_clean(row.get("branch")) for row in city_rows)
    city_current_branches = Counter(
        _clean(row.get("branch")) for row in city_current
    )
    unique_education_source_rows = (
        len(local_rows) + len(platform_native_rows) + len(city_rows)
    )
    expired_count = sum(
        bool(_clean(row.get("end_date")))
        and _clean(row.get("end_date")) < cutoff.isoformat()
        for row in [*local_rows, *platform_native_rows, *city_rows]
    ) + local_reversed_count
    meta.update(
        {
            "network_requests": budget.count,
            "local_source_rows": len(local_rows),
            "local_page_counts": local_page_counts,
            "local_current_count": len(local_current),
            "local_historical_reversed_count": local_reversed_count,
            "local_historical_application_anomaly_count": (
                local_apply_anomaly_count
            ),
            "platform_source_rows": len(platform_rows),
            "platform_page_counts": platform_page_counts,
            "platform_native_rows": len(platform_native_rows),
            "platform_native_current_count": len(platform_current),
            "platform_external_owner_identity_rows": len(
                platform_external_rows
            ),
            "platform_external_visible_duplicate_rows": len(
                platform_visible_duplicates
            ),
            "platform_external_unpublished_test_rows": len(
                platform_unpublished_tests
            ),
            "platform_external_unmatched_rows": 0,
            "city_source_rows": len(city_rows),
            "city_page_counts": city_page_counts,
            "city_current_count": len(city_current),
            "city_undated_closed_count": city_undated_count,
            "city_branch_counts": dict(city_branches),
            "city_current_branch_counts": dict(city_current_branches),
            "source_total": len(local_rows) + len(platform_rows) + len(city_rows),
            "source_rows": len(local_rows) + len(platform_rows) + len(city_rows),
            "unique_education_source_rows": unique_education_source_rows,
            "current_source_count": len(current_rows),
            "expired_count": expired_count,
            "non_current_count": unique_education_source_rows - len(current_rows),
            "returned_count": len(result),
            "detail_pages": len(details.values),
            "detail_errors": 0,
            "application_control_count": sum(
                bool(row.get("reservation_available")) for row in result
            ),
            "status_counts": dict(status_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "duplicate_source_identity_count": len(
                platform_visible_duplicates
            ),
            "privacy_redactions": privacy_redactions,
            "pagination_detected": any(
                last > 1 for last in (local_last, platform_last, city_last)
            ),
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
    return result, BUSAN_NAMGU_PARSER, meta


collect_courses = collect_busan_namgu_education


__all__ = [
    "BUSAN_NAMGU_PROVIDER",
    "BUSAN_CITY_NAMGU_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_NAMGU_MUNICIPALITY_CODE",
    "BUSAN_NAMGU_MUNICIPALITY_NAME",
    "BUSAN_NAMGU_REGISTERED_URL",
    "BUSAN_NAMGU_CANONICAL_URL",
    "BUSAN_NAMGU_URL",
    "BUSAN_CITY_NAMGU_CANDIDATE_URL",
    "BUSAN_CITY_NAMGU_URL",
    "BUSAN_LIFELONG_NAMGU_OFFICE",
    "BUSAN_NAMGU_PARSER",
    "BUSAN_NAMGU_CANDIDATE_IDS",
    "BUSAN_NAMGU_OWNER_BOUNDARY_AUDIT",
    "BUSAN_NAMGU_DISCOVERY_AUDIT",
    "BusanNamguContractError",
    "is_busan_namgu_education_target",
    "is_target",
    "busan_namgu_list_url",
    "busan_namgu_detail_url",
    "busan_namgu_city_list_url",
    "busan_namgu_city_detail_url",
    "busan_namgu_lifelong_list_url",
    "busan_namgu_lifelong_detail_url",
    "canonical_busan_namgu_course_identity",
    "collect_busan_namgu_education",
    "collect_courses",
]
