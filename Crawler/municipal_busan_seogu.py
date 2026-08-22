"""Fail-closed education collector for Busan Seo-gu's official ledgers.

The district education owner is the complete ``edu`` lifelong-learning
catalogue.  The similarly named ``reserve`` candidate is a rendering alias
(and can redirect to the same controller).  The chi application also contains
three public, branch-scoped education ledgers (women's centre, Da-Haengbok
and small libraries); only its signed-in reservation-history menu is outside
the collection boundary.  The final official ledger is the Busan integrated-
reservation partition fixed to Seo-gu (11) and resident-autonomy councils
(33).

All five ledgers are collected atomically.  Every declared list page, the
immediate empty sentinel, all current/future details, and stable first/last
boundaries must agree.  Instructor, telephone, free-form detail, attachment,
location-address and applicant/login page values are never read.  Login and
application controls are retained only after their course identities have
been verified; those controls are never fetched.

The Busan Lifelong Learning Platform office ``OFFICE_00002641`` republishes a
subset of the same Seo-gu ``el_code`` identities.  The ownership audit below
records that exact overlap so the shared platform can suppress this office
instead of creating duplicate courses.
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


BUSAN_SEOGU_PROVIDER = "MUNI_WWW_BSSEOGU_GO_KR_AACF30BC"
BUSAN_SEOGU_REDIRECT_PROVIDER = "MUNI_WWW_BSSEOGU_GO_KR_E6B6EB32"
BUSAN_SEOGU_PRIVATE_HISTORY_PROVIDER = "MUNI_WWW_BSSEOGU_GO_KR_5970CA9E"
BUSAN_LIFELONG_PROVIDER = "MUNI_LLL_BUSAN_GO_KR_944C621B"
BUSAN_SEOGU_MUNICIPALITY_CODE = "2614000000"
BUSAN_SEOGU_MUNICIPALITY_NAME = "부산광역시 서구"

BUSAN_SEOGU_HOST = "www.bsseogu.go.kr"
BUSAN_SEOGU_PATH = "/edu/index.bsseogu"
BUSAN_SEOGU_URL = f"https://{BUSAN_SEOGU_HOST}{BUSAN_SEOGU_PATH}"
BUSAN_SEOGU_LIST_MENU = "DOM_000000703001001000"
BUSAN_SEOGU_DETAIL_MENU = "DOM_000000703001004000"
BUSAN_SEOGU_LOGIN_MENU = "DOM_000000105006004000"
BUSAN_SEOGU_PAGE_SIZE = 10
BUSAN_SEOGU_CHI_PATH = "/chi/index.bsseogu"
BUSAN_SEOGU_CHI_URL = f"https://{BUSAN_SEOGU_HOST}{BUSAN_SEOGU_CHI_PATH}"

BUSAN_SEOGU_REDIRECT_URL = (
    "https://www.bsseogu.go.kr/reserve/index.bsseogu?"
    "nowPage=4&menuCd=DOM_000000703001001000"
)
BUSAN_SEOGU_PRIVATE_HISTORY_URL = (
    "https://www.bsseogu.go.kr/chi/index.bsseogu?"
    "menuCd=DOM_000001006001000000"
)
BUSAN_SEOGU_CHI_DUPLICATE_URL = (
    "https://www.bsseogu.go.kr/chi/index.bsseogu?"
    "menuCd=DOM_000001001001000000"
)

BUSAN_LIFELONG_URL = (
    "https://lll.busan.go.kr/yeyak/ilms/learning/officeList.do"
)
BUSAN_LIFELONG_SEOGU_OFFICE = "OFFICE_00002641"

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_SEOGU_GUGUN = "11"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_SEOGU_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_SEOGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_SEOGU_FETCH_ATTEMPTS = 4
BUSAN_SEOGU_MAX_WORKERS = 8
BUSAN_SEOGU_MAX_HTML_BYTES = 4_000_000
BUSAN_SEOGU_PARSER = (
    "busan_seogu_edu_el_code_all_pages+chi_women_happy_library_all_pages+"
    "busan_reserve_gugun11_office33_all_pages+sentinel+stable_first_last+"
    "all_current_safe_details+identity_bound_login_controls+pii_never_read+"
    "atomic_five_ledger_snapshot"
)
BUSAN_SEOGU_OWNERSHIP_SCOPE = (
    "busan_seogu_complete_public_education_ledgers_and_"
    "busan_city_seogu_resident_autonomy_courses"
)

BUSAN_SEOGU_CANDIDATE_IDS: Mapping[str, str] = {
    "dedicated_education_home": "MUNI_IR_3E1A5287FA4F",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "redirect_alias": "MUNI_IR_AFD656D8AD0E",
    "private_reservation_history": "MUNI_IR_F6065FC650FB",
}

BUSAN_SEOGU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_SEOGU_PROVIDER: {
        "decision": "canonical_district_education_owner",
        "candidate_id": BUSAN_SEOGU_CANDIDATE_IDS["dedicated_education_home"],
        "registered_url": BUSAN_SEOGU_URL,
        "canonical_list_url": (
            f"{BUSAN_SEOGU_URL}?"
            + urlencode(
                (("menuCd", BUSAN_SEOGU_LIST_MENU), ("nowPage", "1"))
            )
        ),
        "operator": "부산광역시 서구 평생학습",
    },
    BUSAN_SEOGU_REDIRECT_PROVIDER: {
        "decision": "render_alias_of_canonical_edu_ledger",
        "candidate_id": BUSAN_SEOGU_CANDIDATE_IDS["redirect_alias"],
        "url": BUSAN_SEOGU_REDIRECT_URL,
        "identity_rule": "same menuCd, nowPage, table rows, and el_code values",
    },
    BUSAN_SEOGU_PRIVATE_HISTORY_PROVIDER: {
        "decision": "exclude_private_my_reservation_history_never_fetch",
        "candidate_id": BUSAN_SEOGU_CANDIDATE_IDS[
            "private_reservation_history"
        ],
        "url": BUSAN_SEOGU_PRIVATE_HISTORY_URL,
        "reason": "나의 예약현황 is an applicant/account boundary, not a catalogue",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "shared_federation_keep_but_suppress_seogu_office_duplicate",
        "candidate_id": BUSAN_SEOGU_CANDIDATE_IDS[
            "busan_lifelong_federation"
        ],
        "url": BUSAN_LIFELONG_URL,
        "office_code": BUSAN_LIFELONG_SEOGU_OFFICE,
        "observed_rows": 20,
        "same_el_code_identity_rows": 20,
        "reason": (
            "every office row links to the dedicated Seo-gu edu detail with "
            "the same el_code; the federation is a stale partial republication"
        ),
    },
    "OFFICIAL_BUSAN_CITY_RESERVATION": {
        "decision": "collect_only_seogu_resident_autonomy_partition",
        "canonical_partition_url": BUSAN_CITY_SEOGU_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_SEOGU_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
        "reason": (
            "the unfiltered Seo-gu location result also contains provincial "
            "operators such as the Child Protection Center"
        ),
    },
    "BUSAN_CITY_DETAIL_384_24458": {
        "decision": "include_in_seogu_resident_autonomy_partition",
        "url": (
            "https://reserve.busan.go.kr/lctre/view?"
            "resveGroupSn=384&progrmSn=24458"
        ),
        "operator": "서구 서대신1동 주민자치회",
    },
    "BUSAN_SEOGU_CHI_PUBLIC_EDUCATION": {
        "decision": "collect_three_public_branch_scoped_ledgers",
        "menus": {
            "women": "DOM_000001001009000000",
            "happy": "DOM_000001001010000000",
            "library": "DOM_000001001011000000",
        },
        "identity_rule": "ledger namespace plus edu_id/el_code",
        "private_menu_never_fetch": "DOM_000001006001000000",
    },
    "BUSAN_SEOGU_CHI_DIGITAL_ARCHIVE": {
        "decision": "exclude_broken_identityless_archive",
        "menu": "DOM_000001001003000000",
        "declared_rows": 224,
        "default_accessible_rows": 212,
        "status_partition_rows": 222,
        "bindable_local_identities": 0,
        "current_search_rows": 0,
        "current_search_window": "2023-01-01..2099-12-31",
        "reason": (
            "default page one throws NumberFormatException, declared rows are "
            "unreachable, and every visible row uses one shared external URL"
        ),
    },
}

BUSAN_SEOGU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_SEOGU_URL,
    "canonical_list_url": BUSAN_SEOGU_OWNER_BOUNDARY_AUDIT[
        BUSAN_SEOGU_PROVIDER
    ]["canonical_list_url"],
    "companion_resident_url": BUSAN_CITY_SEOGU_URL,
    "district_source_rows": 1805,
    "district_data_pages": 181,
    "district_page_counts": {"1-180": 10, "181": 5},
    "district_empty_sentinel_page": 182,
    "district_unique_el_codes": 1805,
    "district_source_status_counts": {"접수중": 8, "접수마감": 1797},
    "district_audited_range_correction_rows": 9,
    "district_current_or_future_rows": 27,
    "district_current_status_counts": {"접수중": 8, "접수마감": 19},
    "district_current_institution_counts": {
        "서구평생학습관": 25,
        "서구 교육진흥과": 2,
    },
    "women_source_rows": 114,
    "women_data_pages": 10,
    "women_empty_sentinel_page": 11,
    "women_current_or_future_rows": 6,
    "women_audited_malformed_education_ranges": 2,
    "happy_source_rows": 1,
    "happy_data_pages": 1,
    "happy_empty_sentinel_page": 2,
    "happy_current_or_future_rows": 1,
    "library_source_rows": 1,
    "library_data_pages": 1,
    "library_empty_sentinel_page": 2,
    "library_current_or_future_rows": 0,
    "city_source_rows": 20,
    "city_data_pages": 2,
    "city_page_counts": {"1": 10, "2": 10},
    "city_empty_sentinel_page": 3,
    "city_current_or_future_rows": 20,
    "city_current_status_counts": {"접수마감": 19, "접수중": 1},
    "city_branch_counts": {
        "서구 동대신1동 주민자치회": 5,
        "서구 서대신1동 주민자치회": 3,
        "서구 서대신4동 주민자치회": 7,
        "서구 암남동 주민자치회": 2,
        "서구 남부민2동 주민자치회": 1,
        "서구 아미동 주민자치회": 2,
    },
    "source_rows": 1941,
    "data_pages": 195,
    "current_or_future_rows": 54,
    "shared_office_rows": 20,
    "shared_office_same_identity_rows": 20,
    "digital_declared_rows_excluded": 224,
    "digital_default_accessible_rows": 212,
    "digital_status_partition_rows": 222,
    "digital_current_search_rows": 0,
}

BUSAN_SEOGU_PII_FIELDS_NEVER_READ = (
    "강사정보 value",
    "교육문의전화 value",
    "상세내용/free-form value",
    "첨부파일 names/content",
    "Busan city 문의 value",
    "Busan city detail 문의전화 value",
    "Busan city reserveDetail/location/강사소개 values",
    "chi 나의 예약현황 rows",
    "chi 여성센터 applicant-table values",
    "chi 다행복 문의전화/접수처/강의내용/강사/비고/첨부파일 values",
    "chi 작은도서관 교육문의전화/상세내용/첨부파일 values",
    "login/applicant form payload",
)


class BusanSeoguContractError(ValueError):
    """Raised when an audited Seo-gu source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or invalid-HTML response."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class _ChiLedger:
    key: str
    list_menu: str
    detail_menu: str
    identity_param: str
    search_type: str
    title_prefix: str
    branch: str
    category: str
    sentinel_kind: str


_CHI_LEDGERS: tuple[_ChiLedger, ...] = (
    _ChiLedger(
        "women",
        "DOM_000001001009000000",
        "DOM_000001001009001000",
        "edu_id",
        "EDU_COURSE",
        "[여성센터]",
        "서구여성센터",
        "여성센터 교육",
        "empty_ul",
    ),
    _ChiLedger(
        "happy",
        "DOM_000001001010000000",
        "DOM_000001001010001000",
        "el_code",
        "P_TITLE",
        "[프로그램]",
        "서구 교육진흥과",
        "다행복교육",
        "empty_ul",
    ),
    _ChiLedger(
        "library",
        "DOM_000001001011000000",
        "DOM_000001001011001000",
        "el_code",
        "A.ECB_NAME",
        "[작은도서관 프로그램]",
        "서구 작은도서관",
        "작은도서관 교육",
        "marker_li",
    ),
)
_CHI_LEDGER_BY_LIST_MENU = {item.list_menu: item for item in _CHI_LEDGERS}
_CHI_LEDGER_BY_DETAIL_MENU = {item.detail_menu: item for item in _CHI_LEDGERS}
_CHI_LEDGER_BY_KEY = {item.key: item for item in _CHI_LEDGERS}

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_LOCAL_ACTION_RE = re.compile(r"^view_flag\(\s*['\"]([1-9]\d*)['\"]\s*\)$")
_CHI_HAPPY_ACTION_RE = re.compile(
    r"^javascript:goDetailPage\(\s*['\"]([1-9]\d*)['\"]\s*\)$"
)
_FLEX_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})-(\d{1,2})-(\d{1,2})\s*~\s*"
    r"(20\d{2})-(\d{1,2})-(\d{1,2})$"
)
_TOTAL_RE = re.compile(
    r"^총\s*(\d+)\s*건의\s*게시물이\s*있습니다\s*"
    r"\(\s*(\d+)\s*/\s*(\d+)\s*페이지\s*\)$"
)
_CAPACITY_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")
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
_CHI_TITLE = "부산광역시 서구 통합예약"
_CHI_PAGE_SIZE = 12
_CHI_LIST_FIELD_LABELS = ("접수기간", "교육기간", "모집인원", "접수방법")
_CHI_STATUS_MAP: Mapping[str, str] = {
    "접수예정": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_CHI_WOMEN_DETAIL_BASE_LABELS = (
    "교육과정",
    "교육기간",
    "교육시간",
    "수강신청기간",
    "교육정원",
    "교육장소",
    "마감여부",
)
_CHI_WOMEN_DETAIL_STATUS: Mapping[str, str] = {
    "접수예정": "SCHEDULED",
    "접수중": "OPEN",
    "마감": "CLOSED",
}
_CHI_HAPPY_DETAIL_LABELS = (
    "모집인원(대기인원)",
    "신청인원",
    "교육기간",
    "접수기간",
    "교육주기",
    "교육시간/요일",
    "학습기관",
    "수강료",
    "교육분야",
    "교육방법",
    "교육장소",
    "교육대상",
    "접수처",
    "문의전화",
    "접수방법",
    "상태",
    "강의내용",
    "강사명",
    "강사 학력 정보",
    "강사 자격증",
    "강사 강의경력",
    "비고",
    "첨부파일",
)
_CHI_HAPPY_SAFE_LABELS = frozenset(_CHI_HAPPY_DETAIL_LABELS) - {
    "접수처",
    "문의전화",
    "강의내용",
    "강사명",
    "강사 학력 정보",
    "강사 자격증",
    "강사 강의경력",
    "비고",
    "첨부파일",
}
_CHI_LIBRARY_DETAIL_LABELS = (
    "강좌명",
    "학습기관",
    "학습기간",
    "접수기간",
    "교육시간",
    "수강료",
    "교육방법",
    "교육대상",
    "교육주기",
    "교육정원/대기정원",
    "교육장소",
    "교육문의전화",
    "접수방법",
    "상태",
    "상세내용",
    "첨부파일",
)
_CHI_LIBRARY_SAFE_LABELS = frozenset(_CHI_LIBRARY_DETAIL_LABELS) - {
    "교육문의전화",
    "상세내용",
    "첨부파일",
}

# Nine expired archive rows contain stable source-order/date mistakes.  They are
# normalized only when both the el_code and exact source value still match.
# A new malformed range therefore remains a fatal contract error.
_AUDITED_RANGE_CORRECTIONS: Mapping[
    tuple[str, str], tuple[str, str, str]
] = {
    ("1784", "education"): (
        "2025-06-27 ~ 2025-06-26",
        "2025-06-26",
        "2025-06-27",
    ),
    ("744", "application"): (
        "2018-03-02 ~ 2018-02-16",
        "2018-02-16",
        "2018-03-02",
    ),
    ("737", "application"): (
        "2018-03-02 ~ 2018-02-16",
        "2018-02-16",
        "2018-03-02",
    ),
    ("74", "application"): (
        "2008-05-07 ~ 2008-04-30",
        "2008-04-30",
        "2008-05-07",
    ),
    ("62", "application"): (
        "2008-01-01 ~ 2008-06-31",
        "2008-01-01",
        "",
    ),
    ("53", "application"): (
        "2008-05-01 ~ 2008-01-01",
        "2008-01-01",
        "2008-05-01",
    ),
    ("52", "application"): (
        "2008-05-01 ~ 2008-01-01",
        "2008-01-01",
        "2008-05-01",
    ),
    ("51", "application"): (
        "2008-05-01 ~ 2008-01-01",
        "2008-01-01",
        "2008-05-01",
    ),
    ("50", "application"): (
        "2008-05-01 ~ 2008-01-01",
        "2008-01-01",
        "2008-05-01",
    ),
    ("88", "women_education"): (
        "2024-06-24~",
        "2024-06-24",
        "",
    ),
    ("87", "women_education"): (
        "2024-02-19~2024-02-02",
        "2024-02-02",
        "2024-02-19",
    ),
}

_LOCAL_TITLE = "평생학습관 < 평생학습강좌 < 강좌 신청 서구평생학습관"
_LOCAL_HEADERS = ("강좌명", "정원", "학습대상", "접수방법", "수강료", "상태")
_LOCAL_SPAN_LABELS = ("접수기간", "교육기간", "교육장소")
_LOCAL_DETAIL_LABELS = (
    "강좌명",
    "학습기관",
    "학습기간",
    "접수기간",
    "교육시간",
    "수강료",
    "강사정보",
    "교육대상",
    "교육주기",
    "교육정원 / 대기정원",
    "교육장소",
    "교육문의전화",
    "접수방법",
    "상태",
    "상세내용",
    "첨부파일",
)
_LOCAL_SAFE_DETAIL_LABELS = frozenset(_LOCAL_DETAIL_LABELS) - {
    "강사정보",
    "교육문의전화",
    "상세내용",
    "첨부파일",
}
_LOCAL_REQUIRED_DETAIL_VALUES = _LOCAL_SAFE_DETAIL_LABELS - {
    "교육주기",
}
_LOCAL_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "대기중": "SCHEDULED",
    "접수마감": "CLOSED",
}

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_DETAIL_LABELS = (
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
_CITY_SAFE_DETAIL_LABELS = frozenset(_CITY_DETAIL_LABELS) - {"문의전화"}
_CITY_STATUS_MAP: Mapping[str, str] = {
    "대기중": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    if node is None:
        return ""
    get_text = getattr(node, "get_text", None)
    return _clean(get_text(" ", strip=True) if callable(get_text) else node)


def _one(values: Sequence[Any], label: str) -> Any:
    if len(values) != 1:
        raise BusanSeoguContractError(
            f"expected one {label}, found {len(values)}"
        )
    return values[0]


def _query_single(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    if len(values) != 1:
        raise BusanSeoguContractError(f"URL {key} is missing or repeated")
    return _clean(values[0])


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def is_busan_seogu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_SEOGU_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == BUSAN_SEOGU_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BUSAN_SEOGU_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def busan_seogu_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise BusanSeoguContractError("district page must be a positive integer")
    return f"{BUSAN_SEOGU_URL}?" + urlencode(
        (("menuCd", BUSAN_SEOGU_LIST_MENU), ("nowPage", str(page)))
    )


def busan_seogu_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanSeoguContractError("detail identity must be a positive integer")
    return f"{BUSAN_SEOGU_URL}?" + urlencode(
        (("menuCd", BUSAN_SEOGU_DETAIL_MENU), ("el_code", value))
    )


def busan_seogu_application_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanSeoguContractError(
            "application identity must be a positive integer"
        )
    return f"https://{BUSAN_SEOGU_HOST}/index.bsseogu?" + urlencode(
        (
            ("menuCd", BUSAN_SEOGU_LOGIN_MENU),
            (
                "returnUrl",
                f"{BUSAN_SEOGU_PATH}?"
                + urlencode(
                    (
                        ("menuCd", BUSAN_SEOGU_DETAIL_MENU),
                        ("el_code", value),
                    )
                ),
            ),
        )
    )


def busan_seogu_chi_list_url(ledger: _ChiLedger | str, page: int = 1) -> str:
    current = (
        _CHI_LEDGER_BY_KEY.get(ledger)
        or _CHI_LEDGER_BY_LIST_MENU.get(ledger)
        if isinstance(ledger, str)
        else ledger
    )
    if current not in _CHI_LEDGERS:
        raise BusanSeoguContractError("unknown chi education ledger")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise BusanSeoguContractError("chi page must be a positive integer")
    return f"{BUSAN_SEOGU_CHI_URL}?" + urlencode(
        (("menuCd", current.list_menu), ("nowPage", str(page)))
    )


def busan_seogu_chi_detail_url(
    ledger: _ChiLedger | str, identity: Any
) -> str:
    current = (
        _CHI_LEDGER_BY_KEY.get(ledger)
        or _CHI_LEDGER_BY_DETAIL_MENU.get(ledger)
        or _CHI_LEDGER_BY_LIST_MENU.get(ledger)
        if isinstance(ledger, str)
        else ledger
    )
    if current not in _CHI_LEDGERS:
        raise BusanSeoguContractError("unknown chi education ledger")
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanSeoguContractError(
            "chi detail identity must be a positive integer"
        )
    return f"{BUSAN_SEOGU_CHI_URL}?" + urlencode(
        (("menuCd", current.detail_menu), (current.identity_param, value))
    )


def busan_seogu_city_list_url(page: int = 1) -> str:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise BusanSeoguContractError("city page must be a positive integer")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", str(page)),
            ("srchGugun", BUSAN_CITY_SEOGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_seogu_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanSeoguContractError(
            "city detail identities must be positive integers"
        )
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def canonical_busan_seogu_course_identity(value: Any) -> str:
    """Return the dedicated/shared ``el_code`` identity for an edu detail URL."""

    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_SEOGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_SEOGU_PATH
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"menuCd", "el_code"}:
        return ""
    if query.get("menuCd") != [BUSAN_SEOGU_DETAIL_MENU]:
        return ""
    identities = query.get("el_code", [])
    if len(identities) != 1 or not _IDENTITY_RE.fullmatch(identities[0]):
        return ""
    return f"bsseogu:{identities[0]}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    return current.get(url, timeout=timeout, allow_redirects=False)


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
        self._lock = threading.Lock()

    def consume(self) -> None:
        with self._lock:
            if self.count >= self.limit:
                raise BusanSeoguContractError(
                    f"max_requests cap exhausted at {self.limit} HTTP attempts"
                )
            self.count += 1


@dataclass(frozen=True)
class _FetchResult:
    values: Mapping[Any, BeautifulSoup]
    errors: tuple[str, ...]
    retries: int
    sessions: int


def _response_soup(response: Any, requested_url: str) -> BeautifulSoup:
    try:
        status = int(getattr(response, "status_code", 200))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise _TransientFetchError(f"HTTP {status}")
    if getattr(response, "history", None):
        raise _TransientFetchError("redirected response")
    headers = getattr(response, "headers", None)
    if headers:
        if headers.get("Location"):
            raise _TransientFetchError("redirect response")
        content_type = _clean(headers.get("Content-Type")).casefold()
        if content_type and "html" not in content_type:
            raise _TransientFetchError("response is not HTML")
    final_url = _clean(getattr(response, "url", ""))
    if final_url and final_url != requested_url:
        raise _TransientFetchError("response URL changed")
    if isinstance(response, BeautifulSoup):
        payload = response.encode("utf-8")
    elif isinstance(response, bytes):
        payload = response
    elif isinstance(response, str):
        payload = response.encode("utf-8")
    else:
        content = getattr(response, "content", None)
        if content is None:
            content = getattr(response, "text", None)
        payload = content.encode("utf-8") if isinstance(content, str) else content
    if not isinstance(payload, bytes) or not payload:
        raise _TransientFetchError("empty HTML response")
    if len(payload) > BUSAN_SEOGU_MAX_HTML_BYTES:
        raise _TransientFetchError("HTML response exceeds byte cap")
    soup = BeautifulSoup(payload, "lxml")

    parsed = urlparse(requested_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    try:
        if parsed.hostname == BUSAN_SEOGU_HOST and parsed.path == BUSAN_SEOGU_PATH:
            menu = _query_single(query, "menuCd")
            if menu == BUSAN_SEOGU_LIST_MENU:
                page_raw = _query_single(query, "nowPage")
                if not page_raw.isdigit() or int(page_raw) < 1:
                    raise BusanSeoguContractError("invalid district request page")
                _local_list_contract(soup, page=int(page_raw))
            elif menu == BUSAN_SEOGU_DETAIL_MENU:
                _local_detail_shell_contract(soup)
            else:
                raise BusanSeoguContractError("unexpected district menu")
        elif (
            parsed.hostname == BUSAN_SEOGU_HOST
            and parsed.path == BUSAN_SEOGU_CHI_PATH
        ):
            menu = _query_single(query, "menuCd")
            if menu in _CHI_LEDGER_BY_LIST_MENU:
                if set(query) != {"menuCd", "nowPage"}:
                    raise BusanSeoguContractError(
                        "unexpected chi list query fields"
                    )
                page_raw = _query_single(query, "nowPage")
                if not page_raw.isdigit() or int(page_raw) < 1:
                    raise BusanSeoguContractError("invalid chi request page")
                _chi_list_contract(
                    soup,
                    ledger=_CHI_LEDGER_BY_LIST_MENU[menu],
                    page=int(page_raw),
                )
            elif menu in _CHI_LEDGER_BY_DETAIL_MENU:
                ledger = _CHI_LEDGER_BY_DETAIL_MENU[menu]
                if set(query) != {"menuCd", ledger.identity_param}:
                    raise BusanSeoguContractError(
                        "unexpected chi detail query fields"
                    )
                identity = _query_single(query, ledger.identity_param)
                if not _IDENTITY_RE.fullmatch(identity):
                    raise BusanSeoguContractError(
                        "invalid chi detail identity"
                    )
                _chi_detail_shell_contract(soup, ledger=ledger)
            else:
                # This explicitly excludes the private reservation-history
                # menu and every unaudited chi controller.
                raise BusanSeoguContractError("unexpected chi menu")
        elif parsed.hostname == BUSAN_CITY_HOST and parsed.path == BUSAN_CITY_LIST_PATH:
            page_raw = _query_single(query, "curPage")
            if not page_raw.isdigit() or int(page_raw) < 1:
                raise BusanSeoguContractError("invalid city request page")
            _city_list_contract(soup, page=int(page_raw))
        elif parsed.hostname == BUSAN_CITY_HOST and parsed.path == BUSAN_CITY_DETAIL_PATH:
            _city_detail_shell_contract(soup)
        else:
            raise BusanSeoguContractError("unexpected request endpoint")
    except BusanSeoguContractError as exc:
        raise _TransientFetchError(f"invalid source response: {exc}") from exc
    return soup


def _fetch_many(
    jobs: Sequence[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
    sleeper: Sleeper,
    budget: _RequestBudget,
) -> _FetchResult:
    if not jobs:
        return _FetchResult({}, (), 0, 0)
    worker_count = min(
        max(1, max_workers), len(jobs), BUSAN_SEOGU_MAX_WORKERS
    )
    chunks: list[list[tuple[Any, str]]] = [[] for _ in range(worker_count)]
    for index, job in enumerate(jobs):
        chunks[index % worker_count].append(job)

    def run_chunk(chunk: Sequence[tuple[Any, str]]):
        values: dict[Any, BeautifulSoup] = {}
        errors: list[str] = []
        retries = 0
        sessions = 1
        current = session_factory()
        try:
            for key, url in chunk:
                messages: list[str] = []
                for attempt in range(1, BUSAN_SEOGU_FETCH_ATTEMPTS + 1):
                    try:
                        budget.consume()
                        values[key] = _response_soup(
                            fetcher(current, url, timeout), url
                        )
                        break
                    except BusanSeoguContractError as exc:
                        messages.append(_clean(exc))
                        break
                    except Exception as exc:
                        messages.append(
                            f"attempt {attempt}: {type(exc).__name__}: "
                            f"{_clean(exc)}"
                        )
                        if attempt >= BUSAN_SEOGU_FETCH_ATTEMPTS:
                            break
                        retries += 1
                        _close_quietly(current)
                        current = session_factory()
                        sessions += 1
                        sleeper(min(0.25 * attempt, 0.75))
                if key not in values:
                    errors.append(f"{key}: {'; '.join(messages)}")
        finally:
            _close_quietly(current)
        return values, errors, retries, sessions

    combined: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    retries = 0
    sessions = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(run_chunk, chunk) for chunk in chunks if chunk
        ]
        for future in as_completed(futures):
            values, current_errors, current_retries, current_sessions = (
                future.result()
            )
            combined.update(values)
            errors.extend(current_errors)
            retries += current_retries
            sessions += current_sessions
    return _FetchResult(combined, tuple(errors), retries, sessions)


def _strict_flexible_range(
    value: Any,
    label: str,
    *,
    identity: str = "",
    kind: str = "",
) -> tuple[str, str]:
    raw = _clean(value)
    audited = _AUDITED_RANGE_CORRECTIONS.get((identity, kind))
    if audited and audited[0] == raw:
        return audited[1], audited[2]
    match = _FLEX_DATE_RANGE_RE.fullmatch(raw)
    if not match:
        raise BusanSeoguContractError(f"{label} changed")
    try:
        start = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        end = date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
    except ValueError as exc:
        raise BusanSeoguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BusanSeoguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _label_value(value: Any, expected: str) -> str:
    raw = _clean(value)
    prefix = f"{expected} :"
    if not raw.startswith(prefix):
        raise BusanSeoguContractError(f"expected {expected} list field")
    return _clean(raw[len(prefix) :])


def _application_method_tokens(value: Any, label: str) -> frozenset[str]:
    raw = _clean(value)
    tokens = re.findall(r"온라인접수|전화접수|방문접수", raw)
    compact = re.sub(r"[\s,./+]+", "", raw)
    if not tokens or "".join(tokens) != compact or len(tokens) != len(set(tokens)):
        raise BusanSeoguContractError(f"{label} vocabulary changed")
    return frozenset(tokens)


def _city_application_method_parts(value: Any, label: str) -> tuple[str, ...]:
    raw = _clean(value)
    parts = tuple(part for item in raw.split(",") if (part := _clean(item)))
    if not parts:
        raise BusanSeoguContractError(f"{label} is empty")
    return parts


def _local_list_contract(
    soup: BeautifulSoup, *, page: int
) -> tuple[int, int, Tag]:
    if _text(_one(soup.select("title"), "district list title")) != _LOCAL_TITLE:
        raise BusanSeoguContractError("district list title changed")
    form = _one(
        soup.select("#contents form.rfc_bbs_searchForm"),
        "district search form",
    )
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != BUSAN_SEOGU_PATH
    ):
        raise BusanSeoguContractError("district search form changed")
    for name, expected in (
        ("searchType", "A.ECB_NAME"),
        ("el_code", ""),
        ("menuCd", BUSAN_SEOGU_LIST_MENU),
        ("keyword", ""),
    ):
        field = _one(form.select(f"[name='{name}']"), f"district {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSeoguContractError(f"district form {name} changed")

    total = _one(soup.select("#contents div.board-top div.total"), "district total")
    match = _TOTAL_RE.fullmatch(_text(total))
    if not match:
        raise BusanSeoguContractError("district total/page declaration changed")
    declared_total, displayed_page, displayed_last = map(int, match.groups())
    expected_last = max(1, math.ceil(declared_total / BUSAN_SEOGU_PAGE_SIZE))
    if displayed_page != page:
        raise BusanSeoguContractError("district displayed page differs from request")
    if displayed_last != expected_last:
        raise BusanSeoguContractError("district displayed last page is inconsistent")
    if page > displayed_last + 1:
        raise BusanSeoguContractError("district page passed sentinel boundary")

    table = _one(
        soup.select("#contents div.board-list-wrap.lecture-list > table"),
        "district course table",
    )
    headers = tuple(_text(item) for item in table.select("thead > tr > th"))
    if headers != _LOCAL_HEADERS:
        raise BusanSeoguContractError("district course headers changed")
    body = _one(table.select(":scope > tbody"), "district table body")
    return declared_total, displayed_last, body


def _parse_local_list_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last_page, body = _local_list_contract(soup, page=page)
    if expected_total is not None and total != expected_total:
        raise BusanSeoguContractError(
            f"district page {page}: declared total changed"
        )
    rows: list[dict[str, Any]] = []
    items = body.find_all("tr", recursive=False)
    if page == last_page + 1:
        empty_row = _one(items, "district empty sentinel row")
        empty_cell = _one(
            empty_row.find_all("td", recursive=False),
            "district empty sentinel cell",
        )
        if (
            _clean(empty_cell.get("colspan")) != str(len(_LOCAL_HEADERS))
            or _text(empty_cell) != "등록된 데이터가 없습니다."
        ):
            raise BusanSeoguContractError(
                "district empty sentinel marker changed"
            )
        return [], total, last_page
    for position, item in enumerate(items, 1):
        if "lecture-name" not in (item.get("class") or []):
            raise BusanSeoguContractError(
                f"district page {page} row {position}: row class changed"
            )
        cells = item.find_all("td", recursive=False)
        if len(cells) != len(_LOCAL_HEADERS):
            raise BusanSeoguContractError(
                f"district page {page} row {position}: cell count changed"
            )
        link = _one(
            cells[0].select(":scope > a[onclick]"), "district identity link"
        )
        if _clean(link.get("href")) != "#":
            raise BusanSeoguContractError("district detail href changed")
        action = _LOCAL_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanSeoguContractError(
                f"district page {page} row {position}: identity action changed"
            )
        identity = action.group(1)
        title_node = _one(link.select(":scope > span.btxt"), "district title")
        source_title = _text(title_node)
        if not source_title:
            raise BusanSeoguContractError("district title is empty")
        spans = link.select(":scope > span.stxt")
        if len(spans) != len(_LOCAL_SPAN_LABELS):
            raise BusanSeoguContractError("district list fields changed")
        list_values = {
            label: _label_value(_text(span), label)
            for label, span in zip(_LOCAL_SPAN_LABELS, spans)
        }
        apply_start, apply_end = _strict_flexible_range(
            list_values["접수기간"],
            "district application period",
            identity=identity,
            kind="application",
        )
        start, end = _strict_flexible_range(
            list_values["교육기간"],
            "district education period",
            identity=identity,
            kind="education",
        )
        status_node = _one(cells[5].select(":scope > span"), "district status")
        source_status = _text(status_node)
        if source_status not in _LOCAL_STATUS_MAP:
            raise BusanSeoguContractError(
                f"district page {page} row {position}: unknown status"
            )
        title = re.sub(r"^\d+[.]\s*", "", source_title).strip()
        capacity_raw = _text(cells[1])
        capacity_total = int(capacity_raw) if capacity_raw.isdigit() else None
        target = _text(cells[2])
        method = _text(cells[3])
        fee = _text(cells[4])
        raw_url = busan_seogu_detail_url(identity)
        rows.append(
            {
                "provider": BUSAN_SEOGU_PROVIDER,
                "provider_course_id": f"edu:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": "부산광역시 서구 평생학습",
                "branch_code": "bsseogu-edu",
                "preserve_branch": True,
                "category": "평생학습강좌",
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": method,
                "reservation_available": False,
                "status": _LOCAL_STATUS_MAP[source_status],
                "fee": fee,
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": "",
                "target": target,
                "capacity_total": capacity_total,
                "capacity_current": None,
                "venue_name": list_values["교육장소"],
                "provider_organizer": "부산광역시 서구",
                "municipality_code": BUSAN_SEOGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_SEOGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "서구",
                "collection_category": "평생학습",
                "domain_category": "평생교육",
                "operator_type": "지자체/공공기관",
                "source_group": "lifelong_learning",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "parser": BUSAN_SEOGU_PARSER,
                    "source_catalog": "busan_seogu_lifelong_education",
                    "source_identity": identity,
                    "source_el_code": identity,
                    "source_page": page,
                    "source_position": position,
                    "source_title": source_title,
                    "source_status": source_status,
                    "source_application_period": list_values["접수기간"],
                    "source_education_period": list_values["교육기간"],
                    "source_application_method": method,
                    "audited_application_range_corrected": (
                        (identity, "application")
                        in _AUDITED_RANGE_CORRECTIONS
                    ),
                    "audited_education_range_corrected": (
                        (identity, "education") in _AUDITED_RANGE_CORRECTIONS
                    ),
                    "detail_verified": False,
                    "service_family": "education",
                },
            }
        )
    return rows, total, last_page


def _direct_text_without_child_tags(node: Tag) -> str:
    return _clean(
        " ".join(
            str(child)
            for child in node.children
            if isinstance(child, NavigableString)
        )
    )


def _chi_list_contract(
    soup: BeautifulSoup,
    *,
    ledger: _ChiLedger,
    page: int,
) -> tuple[int, int, Tag]:
    if _text(_one(soup.select("title"), "chi list title")) != _CHI_TITLE:
        raise BusanSeoguContractError(f"chi {ledger.key} list title changed")
    form = _one(
        soup.select("#contents form.rfc_bbs_searchForm"),
        f"chi {ledger.key} search form",
    )
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path
        != "/reserve/index.bsseogu"
    ):
        raise BusanSeoguContractError(f"chi {ledger.key} search form changed")
    for name, expected in (
        ("nowPage", "1"),
        ("nowBlock", "0"),
        ("searchType", ledger.search_type),
        (ledger.identity_param, ""),
        ("menuCd", ledger.list_menu),
        ("el_sdate", ""),
        ("el_edate", ""),
        ("keyword", ""),
    ):
        field = _one(form.select(f"[name='{name}']"), f"chi {ledger.key} {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSeoguContractError(
                f"chi {ledger.key} form {name} changed"
            )

    wrapper = _one(
        soup.select("#contents .courseList-wrap"),
        f"chi {ledger.key} course list",
    )
    total_node = _one(
        wrapper.select(":scope > div.total > p"),
        f"chi {ledger.key} total",
    )
    match = _TOTAL_RE.fullmatch(_text(total_node))
    if not match:
        raise BusanSeoguContractError(
            f"chi {ledger.key} total/page declaration changed"
        )
    total, displayed_page, displayed_last = map(int, match.groups())
    expected_last = max(1, math.ceil(total / _CHI_PAGE_SIZE))
    if displayed_page != page or displayed_last != expected_last:
        raise BusanSeoguContractError(
            f"chi {ledger.key} displayed page/last changed"
        )
    if page > displayed_last + 1:
        raise BusanSeoguContractError(
            f"chi {ledger.key} page passed sentinel boundary"
        )
    listing = _one(
        wrapper.find_all("ul", recursive=False),
        f"chi {ledger.key} list body",
    )
    return total, displayed_last, listing


def _parse_chi_list_page(
    soup: BeautifulSoup,
    *,
    ledger: _ChiLedger,
    page: int,
    expected_total: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last_page, listing = _chi_list_contract(
        soup, ledger=ledger, page=page
    )
    if expected_total is not None and total != expected_total:
        raise BusanSeoguContractError(
            f"chi {ledger.key} page {page}: declared total changed"
        )
    items = listing.find_all("li", recursive=False)
    if page == last_page + 1:
        if ledger.sentinel_kind == "empty_ul":
            if items:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} empty sentinel changed"
                )
        elif ledger.sentinel_kind == "marker_li":
            marker = _one(items, f"chi {ledger.key} sentinel marker")
            if (
                marker.find(True) is not None
                or _text(marker) != "등록된 데이터가 없습니다."
            ):
                raise BusanSeoguContractError(
                    f"chi {ledger.key} empty sentinel changed"
                )
        else:
            raise BusanSeoguContractError("unknown chi sentinel contract")
        return [], total, last_page

    rows: list[dict[str, Any]] = []
    for position, item in enumerate(items, 1):
        direct = item.find_all(recursive=False)
        if len(direct) != 2 or direct[0].name != "a" or direct[1].name != "span":
            raise BusanSeoguContractError(
                f"chi {ledger.key} page {page} row {position}: card changed"
            )
        link, closed_control = direct
        if "btn-link" not in (closed_control.get("class") or []):
            raise BusanSeoguContractError(
                f"chi {ledger.key} row control changed"
            )
        if ledger.key == "happy":
            action = _CHI_HAPPY_ACTION_RE.fullmatch(_clean(link.get("href")))
        else:
            action = _LOCAL_ACTION_RE.fullmatch(_clean(link.get("onclick")))
            if _clean(link.get("href")) != "#":
                action = None
        if not action:
            raise BusanSeoguContractError(
                f"chi {ledger.key} page {page} row {position}: identity changed"
            )
        identity = action.group(1)
        spans = link.find_all("span", recursive=False)
        if len(spans) != 6:
            raise BusanSeoguContractError(
                f"chi {ledger.key} list fields changed"
            )
        status_node, title_node, *fields = spans
        if "state" not in (status_node.get("class") or []):
            raise BusanSeoguContractError(f"chi {ledger.key} status node changed")
        source_status = _text(status_node)
        if source_status not in _CHI_STATUS_MAP:
            raise BusanSeoguContractError(f"chi {ledger.key} status changed")
        source_title = _text(title_node)
        if not source_title.startswith(ledger.title_prefix):
            raise BusanSeoguContractError(
                f"chi {ledger.key} title namespace changed"
            )
        title = _clean(source_title[len(ledger.title_prefix) :])
        if not title:
            raise BusanSeoguContractError(f"chi {ledger.key} title is empty")

        values: dict[str, str] = {}
        observed_labels: list[str] = []
        for field in fields:
            label_node = _one(
                field.find_all("span", recursive=False),
                f"chi {ledger.key} field label",
            )
            label = _text(label_node)
            value = _direct_text_without_child_tags(field)
            if not value:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} {label} is empty"
                )
            observed_labels.append(label)
            values[label] = value
        first_label = observed_labels[0] if observed_labels else ""
        allowed_first = {"접수기간"}
        if ledger.key == "women":
            allowed_first.add("추가접수기간")
        if (
            first_label not in allowed_first
            or tuple(observed_labels[1:]) != _CHI_LIST_FIELD_LABELS[1:]
            or len(values) != 4
        ):
            raise BusanSeoguContractError(
                f"chi {ledger.key} list field labels changed"
            )
        apply_start, apply_end = _strict_flexible_range(
            values[first_label],
            f"chi {ledger.key} application period",
            identity=identity,
            kind=f"{ledger.key}_application",
        )
        start, end = _strict_flexible_range(
            values["교육기간"],
            f"chi {ledger.key} education period",
            identity=identity,
            kind=f"{ledger.key}_education",
        )
        capacity_match = re.fullmatch(r"(\d+)\s*명", values["모집인원"])
        if not capacity_match:
            raise BusanSeoguContractError(
                f"chi {ledger.key} capacity changed"
            )
        raw_url = busan_seogu_chi_detail_url(ledger, identity)
        rows.append(
            {
                "provider": BUSAN_SEOGU_PROVIDER,
                "provider_course_id": f"{ledger.key}:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": ledger.branch,
                "branch_code": f"bsseogu-{ledger.key}",
                "preserve_branch": True,
                "category": ledger.category,
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": values["접수방법"],
                "reservation_available": False,
                "status": _CHI_STATUS_MAP[source_status],
                "fee": "",
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": "",
                "target": "",
                "capacity_total": int(capacity_match.group(1)),
                "capacity_current": None,
                "venue_name": "",
                "provider_organizer": "부산광역시 서구",
                "municipality_code": BUSAN_SEOGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_SEOGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "서구",
                "collection_category": "평생학습",
                "domain_category": "평생교육",
                "operator_type": "지자체/공공기관",
                "source_group": "lifelong_learning",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "parser": BUSAN_SEOGU_PARSER,
                    "source_catalog": f"busan_seogu_chi_{ledger.key}",
                    "source_identity": identity,
                    "source_identity_param": ledger.identity_param,
                    "canonical_scoped_identity": (
                        f"bsseogu:{ledger.key}:{identity}"
                    ),
                    "source_page": page,
                    "source_position": position,
                    "source_title": source_title,
                    "source_status": source_status,
                    "source_application_label": first_label,
                    "source_application_period": values[first_label],
                    "source_education_period": values["교육기간"],
                    "source_application_method": values["접수방법"],
                    "audited_application_range_corrected": (
                        (identity, f"{ledger.key}_application")
                        in _AUDITED_RANGE_CORRECTIONS
                    ),
                    "audited_education_range_corrected": (
                        (identity, f"{ledger.key}_education")
                        in _AUDITED_RANGE_CORRECTIONS
                    ),
                    "audited_expired_without_end": (
                        ledger.key == "women" and identity == "88"
                    ),
                    "detail_verified": False,
                    "service_family": "education",
                },
            }
        )
    return rows, total, last_page


def _local_detail_shell_contract(soup: BeautifulSoup) -> None:
    if _text(_one(soup.select("title"), "district detail title")) != _LOCAL_TITLE:
        raise BusanSeoguContractError("district detail title changed")
    _one(soup.select("#contents table"), "district detail table")
    _one(
        soup.select("#contents form[name='rfc_bbs_searchForm']"),
        "district detail return form",
    )


def _validate_local_application_href(value: Any, identity: str) -> str:
    absolute = urljoin(BUSAN_SEOGU_URL, _clean(value))
    parsed = urlparse(absolute)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_SEOGU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != "/index.bsseogu"
        or parsed.params
        or parsed.fragment
    ):
        raise BusanSeoguContractError("unsafe district application/login URL")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"menuCd", "returnUrl"}:
        raise BusanSeoguContractError("district login URL query changed")
    if _query_single(query, "menuCd") != BUSAN_SEOGU_LOGIN_MENU:
        raise BusanSeoguContractError("district login menu changed")
    nested = urlparse(_query_single(query, "returnUrl"))
    nested_query = parse_qs(nested.query, keep_blank_values=True)
    if (
        nested.scheme
        or nested.netloc
        or nested.path != BUSAN_SEOGU_PATH
        or nested.params
        or nested.fragment
        or set(nested_query) != {"menuCd", "el_code"}
        or _query_single(nested_query, "menuCd") != BUSAN_SEOGU_DETAIL_MENU
        or _query_single(nested_query, "el_code") != identity
    ):
        raise BusanSeoguContractError("district login return identity changed")
    expected = busan_seogu_application_url(identity)
    if absolute != expected:
        raise BusanSeoguContractError("district login URL canonical form changed")
    return absolute


def _parse_local_detail(
    soup: BeautifulSoup, row: Mapping[str, Any]
) -> dict[str, Any]:
    _local_detail_shell_contract(soup)
    table = _one(soup.select("#contents table"), "district detail table")
    labels: list[str] = []
    safe: dict[str, str] = {}
    for table_row in table.select(":scope > tbody > tr"):
        cells = table_row.find_all(["th", "td"], recursive=False)
        if not cells or len(cells) % 2:
            raise BusanSeoguContractError("district detail row shape changed")
        for index in range(0, len(cells), 2):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise BusanSeoguContractError("district detail label/value changed")
            label = _text(cells[index])
            labels.append(label)
            if label in _LOCAL_SAFE_DETAIL_LABELS:
                safe[label] = _text(cells[index + 1])
            elif label not in _LOCAL_DETAIL_LABELS:
                raise BusanSeoguContractError(
                    "unknown district detail field at PII boundary"
                )
            # The value of every non-safe label is intentionally never read.
    if tuple(labels) != _LOCAL_DETAIL_LABELS:
        raise BusanSeoguContractError("district detail labels changed")
    if set(safe) != _LOCAL_SAFE_DETAIL_LABELS:
        raise BusanSeoguContractError("district safe detail fields changed")
    if any(not safe[label] for label in _LOCAL_REQUIRED_DETAIL_VALUES):
        raise BusanSeoguContractError("district safe detail value is empty")

    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("source_el_code"))
    source_title = _clean(raw.get("source_title"))
    if safe["강좌명"] != source_title:
        raise BusanSeoguContractError("district list/detail title differs")
    start, end = _strict_flexible_range(
        safe["학습기간"], "district detail education period"
    )
    apply_start, apply_end = _strict_flexible_range(
        safe["접수기간"], "district detail application period"
    )
    if (start, end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        raise BusanSeoguContractError("district list/detail dates differ")
    if safe["상태"] != _clean(raw.get("source_status")):
        raise BusanSeoguContractError("district list/detail status differs")
    if _application_method_tokens(
        safe["접수방법"], "district detail application method"
    ) != _application_method_tokens(
        row.get("application_method_raw"), "district list application method"
    ):
        raise BusanSeoguContractError("district list/detail method differs")
    if safe["교육대상"] != _clean(row.get("target")):
        raise BusanSeoguContractError("district list/detail target differs")
    branch = safe["학습기관"]
    if not branch.startswith("서구"):
        raise BusanSeoguContractError("district detail left Seo-gu owner")

    form = _one(
        soup.select("#contents form[name='rfc_bbs_searchForm']"),
        "district detail return form",
    )
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path
        != "/reserve/index.bsseogu"
    ):
        raise BusanSeoguContractError("district detail return form changed")
    for name, expected in (
        ("el_code", identity),
        ("menuCd", BUSAN_SEOGU_LIST_MENU),
    ):
        field = _one(form.select(f"input[name='{name}']"), f"detail {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSeoguContractError("district detail identity changed")

    application_controls = [
        anchor
        for anchor in soup.select("#contents a[href]")
        if _text(anchor) == "신청하기"
    ]
    normalized_status = _LOCAL_STATUS_MAP[safe["상태"]]
    application_url = ""
    application_type = "INFO_ONLY"
    reservation_available = False
    if normalized_status == "OPEN":
        control = _one(application_controls, "district application control")
        application_url = _validate_local_application_href(
            control.get("href"), identity
        )
        if "check_back" not in _clean(control.get("onclick")):
            raise BusanSeoguContractError("district application action changed")
        application_type = "LOGIN_REQUIRED"
        reservation_available = True
    elif application_controls:
        raise BusanSeoguContractError(
            "non-open district detail exposed an application control"
        )

    capacity = _CAPACITY_RE.fullmatch(safe["교육정원 / 대기정원"])
    if not capacity:
        raise BusanSeoguContractError("district detail capacity changed")
    result = dict(row)
    result.update(
        {
            "application_url": application_url,
            "application_type": application_type,
            "reservation_available": reservation_available,
            "branch": branch,
            "branch_code": f"bsseogu-edu-{identity}",
            "venue_name": safe["교육장소"],
            "fee": safe["수강료"],
            "period": f"{start} ~ {end}",
            "apply_period": f"{apply_start} ~ {apply_end}",
            "schedule_raw": safe["교육시간"],
            "capacity_total": int(capacity.group(1)),
            "capacity_waiting": int(capacity.group(2)),
            "provider_organizer": branch,
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": safe["상태"],
            "source_institution": branch,
            "source_venue": safe["교육장소"],
            "source_schedule": safe["교육시간"],
            "source_fee": safe["수강료"],
            "source_capacity": safe["교육정원 / 대기정원"],
            "application_control_present": bool(application_url),
            "application_control_identity_verified": bool(application_url),
            "login_applicant_boundary_never_fetched": True,
            "instructor_value_never_read": True,
            "inquiry_phone_value_never_read": True,
            "free_form_detail_never_read": True,
            "attachments_never_read": True,
        }
    )
    result["raw_fields"] = raw
    return result


def _chi_detail_shell_contract(
    soup: BeautifulSoup, *, ledger: _ChiLedger
) -> None:
    if _text(_one(soup.select("title"), "chi detail title")) != _CHI_TITLE:
        raise BusanSeoguContractError(f"chi {ledger.key} detail title changed")
    _one(
        soup.select("#contents > form[name='rfc_bbs_searchForm']"),
        f"chi {ledger.key} detail form",
    )
    selector = (
        "#contents .board-write-wrap > table:not(.tbl-type01)"
        if ledger.key == "women"
        else "#contents .board-view-wrap02 > table"
    )
    _one(soup.select(selector), f"chi {ledger.key} safe detail table")


def _validate_chi_detail_form(
    soup: BeautifulSoup,
    *,
    ledger: _ChiLedger,
    identity: str,
) -> None:
    form = _one(
        soup.select("#contents > form[name='rfc_bbs_searchForm']"),
        f"chi {ledger.key} detail form",
    )
    action = urlparse(_clean(form.get("action")))
    expected_action_menu = (
        ledger.list_menu
        if ledger.key == "women"
        else "DOM_000001001001000000"
    )
    if (
        _clean(form.get("method")).casefold() != "get"
        or action.path != "/reserve/index.bsseogu"
        or parse_qs(action.query, keep_blank_values=True)
        != {"menuCd": [expected_action_menu]}
    ):
        raise BusanSeoguContractError(
            f"chi {ledger.key} detail return form changed"
        )
    expected_hidden_menu = (
        "DOM_000001001001000000"
        if ledger.key == "library"
        else ledger.list_menu
    )
    for name, expected in (
        ("nowPage", "1"),
        ("nowBlock", "0"),
        (ledger.identity_param, identity),
        ("searchType", ledger.search_type if ledger.key != "women" else ""),
        ("menuCd", expected_hidden_menu),
        ("el_sdate", ""),
        ("el_edate", ""),
        ("keyword", ""),
    ):
        field = _one(
            form.select(f"input[name='{name}']"),
            f"chi {ledger.key} detail {name}",
        )
        if _clean(field.get("value")) != expected:
            raise BusanSeoguContractError(
                f"chi {ledger.key} detail identity/form changed"
            )


def _strict_detail_date_pair(value: Any, label: str) -> tuple[str, str]:
    raw = _clean(value)
    values = _CITY_DETAIL_DATE_RE.findall(raw)
    if len(values) != 2:
        raise BusanSeoguContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(item) for item in values)
    except ValueError as exc:
        raise BusanSeoguContractError(f"{label} has invalid date") from exc
    if end < start:
        raise BusanSeoguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _parse_chi_women_detail(
    soup: BeautifulSoup, row: Mapping[str, Any], ledger: _ChiLedger
) -> dict[str, Any]:
    _chi_detail_shell_contract(soup, ledger=ledger)
    table = _one(
        soup.select("#contents .board-write-wrap > table:not(.tbl-type01)"),
        "chi women safe detail table",
    )
    labels: list[str] = []
    safe: dict[str, str] = {}
    body = _one(table.find_all("tbody", recursive=False), "chi women table body")
    for table_row in body.find_all("tr", recursive=False):
        cells = table_row.find_all(["th", "td"], recursive=False)
        if (
            len(cells) != 2
            or cells[0].name != "th"
            or cells[1].name != "td"
        ):
            raise BusanSeoguContractError("chi women detail row changed")
        label = _text(cells[0])
        allowed = set(_CHI_WOMEN_DETAIL_BASE_LABELS) | {"추가신청기간"}
        if label not in allowed:
            # Never inspect a value for a newly introduced field.
            raise BusanSeoguContractError(
                "unknown chi women detail field at PII boundary"
            )
        labels.append(label)
        safe[label] = _text(cells[1])
    expected_with_extra = (
        _CHI_WOMEN_DETAIL_BASE_LABELS[:5]
        + ("추가신청기간",)
        + _CHI_WOMEN_DETAIL_BASE_LABELS[5:]
    )
    if tuple(labels) not in {
        _CHI_WOMEN_DETAIL_BASE_LABELS,
        expected_with_extra,
    }:
        raise BusanSeoguContractError("chi women detail labels changed")
    if any(not value for value in safe.values()):
        raise BusanSeoguContractError("chi women safe detail value is empty")

    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    _validate_chi_detail_form(soup, ledger=ledger, identity=identity)
    if safe["교육과정"] != _clean(row.get("title")):
        raise BusanSeoguContractError("chi women list/detail title differs")
    start, end = _strict_detail_date_pair(
        safe["교육기간"], "chi women detail education period"
    )
    if (start, end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        raise BusanSeoguContractError("chi women list/detail dates differ")
    application_label = _clean(raw.get("source_application_label"))
    detail_application_label = (
        "추가신청기간"
        if application_label == "추가접수기간"
        else "수강신청기간"
    )
    if detail_application_label not in safe:
        raise BusanSeoguContractError(
            "chi women effective application period is missing"
        )
    apply_start, apply_end = _strict_detail_date_pair(
        safe[detail_application_label],
        "chi women detail application period",
    )
    if (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        raise BusanSeoguContractError(
            "chi women list/detail application dates differ"
        )
    detail_status = safe["마감여부"]
    if detail_status not in _CHI_WOMEN_DETAIL_STATUS:
        raise BusanSeoguContractError("chi women detail status changed")
    normalized_status = _CHI_WOMEN_DETAIL_STATUS[detail_status]
    if normalized_status != row.get("status"):
        raise BusanSeoguContractError("chi women list/detail status differs")
    if normalized_status != "CLOSED":
        raise BusanSeoguContractError(
            "chi women non-closed application contract requires review"
        )
    capacity = re.fullmatch(r"(\d+)\s*명", safe["교육정원"])
    if not capacity:
        raise BusanSeoguContractError("chi women detail capacity changed")

    result = dict(row)
    result.update(
        {
            "schedule_raw": safe["교육시간"],
            "capacity_total": int(capacity.group(1)),
            "venue_name": safe["교육장소"],
            "period": f"{start} ~ {end}",
            "apply_period": f"{apply_start} ~ {apply_end}",
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": detail_status,
            "source_venue": safe["교육장소"],
            "source_schedule": safe["교육시간"],
            "source_capacity": safe["교육정원"],
            "detail_effective_application_label": detail_application_label,
            "applicant_table_values_never_read": True,
            "application_control_present": False,
        }
    )
    result["raw_fields"] = raw
    return result


def _parse_chi_happy_detail(
    soup: BeautifulSoup, row: Mapping[str, Any], ledger: _ChiLedger
) -> dict[str, Any]:
    _chi_detail_shell_contract(soup, ledger=ledger)
    table = _one(
        soup.select("#contents .board-view-wrap02 > table"),
        "chi happy safe detail table",
    )
    body = _one(table.find_all("tbody", recursive=False), "chi happy table body")
    table_rows = body.find_all("tr", recursive=False)
    if not table_rows:
        raise BusanSeoguContractError("chi happy detail table is empty")
    title_cells = table_rows[0].find_all(["th", "td"], recursive=False)
    if (
        len(title_cells) != 1
        or title_cells[0].name != "td"
        or _clean(title_cells[0].get("colspan")) != "4"
    ):
        raise BusanSeoguContractError("chi happy title row changed")
    detail_title = _text(title_cells[0])
    labels: list[str] = []
    safe: dict[str, str] = {}
    for table_row in table_rows[1:]:
        cells = table_row.find_all(["th", "td"], recursive=False)
        if not cells or len(cells) % 2:
            raise BusanSeoguContractError("chi happy detail row shape changed")
        for index in range(0, len(cells), 2):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise BusanSeoguContractError("chi happy label/value changed")
            label = _text(cells[index])
            labels.append(label)
            if label in _CHI_HAPPY_SAFE_LABELS:
                safe[label] = _text(cells[index + 1])
            elif label not in _CHI_HAPPY_DETAIL_LABELS:
                raise BusanSeoguContractError(
                    "unknown chi happy detail field at PII boundary"
                )
            # Unsafe sibling values are deliberately not read.
    if tuple(labels) != _CHI_HAPPY_DETAIL_LABELS:
        raise BusanSeoguContractError("chi happy detail labels changed")
    if set(safe) != _CHI_HAPPY_SAFE_LABELS:
        raise BusanSeoguContractError("chi happy safe fields changed")
    for label, value in safe.items():
        if label != "교육대상" and not value:
            raise BusanSeoguContractError(
                f"chi happy safe detail {label} is empty"
            )

    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    _validate_chi_detail_form(soup, ledger=ledger, identity=identity)
    if detail_title != _clean(row.get("title")):
        raise BusanSeoguContractError("chi happy list/detail title differs")
    start, end = _strict_detail_date_pair(
        safe["교육기간"], "chi happy detail education period"
    )
    apply_start, apply_end = _strict_detail_date_pair(
        safe["접수기간"], "chi happy detail application period"
    )
    if (start, end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        raise BusanSeoguContractError("chi happy list/detail dates differ")
    source_status = safe["상태"]
    if (
        source_status not in _CHI_STATUS_MAP
        or _CHI_STATUS_MAP[source_status] != row.get("status")
    ):
        raise BusanSeoguContractError("chi happy list/detail status differs")
    if row.get("status") != "CLOSED":
        raise BusanSeoguContractError(
            "chi happy non-closed application contract requires review"
        )
    if safe["접수방법"] != _clean(row.get("application_method_raw")):
        raise BusanSeoguContractError("chi happy list/detail method differs")
    misleading = _one(
        soup.select(
            "#contents p.tc.btnwarp > a.bg-btn[href='javascript:check_back();']"
        ),
        "chi happy closed application control",
    )
    if _text(_one(misleading.select(":scope > span.apply"), "apply label")) != "신청하기":
        raise BusanSeoguContractError("chi happy closed control changed")
    capacity = re.fullmatch(
        r"(\d+)\s*명\s*\(\s*(\d+)\s*명\s*\)",
        safe["모집인원(대기인원)"],
    )
    current = re.fullmatch(r"(\d+)\s*명", safe["신청인원"])
    if not capacity or not current:
        raise BusanSeoguContractError("chi happy capacity changed")

    result = dict(row)
    result.update(
        {
            "branch": safe["학습기관"],
            "branch_code": f"bsseogu-happy-{identity}",
            "venue_name": safe["교육장소"],
            "target": safe["교육대상"],
            "fee": safe["수강료"],
            "category": safe["교육분야"],
            "schedule_raw": safe["교육시간/요일"],
            "capacity_total": int(capacity.group(1)),
            "capacity_waiting": int(capacity.group(2)),
            "capacity_current": int(current.group(1)),
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": source_status,
            "source_institution": safe["학습기관"],
            "source_venue": safe["교육장소"],
            "source_schedule": safe["교육시간/요일"],
            "source_fee": safe["수강료"],
            "source_education_method": safe["교육방법"],
            "closed_application_control_ignored": True,
            "inquiry_phone_value_never_read": True,
            "application_office_value_never_read": True,
            "free_form_detail_never_read": True,
            "instructor_values_never_read": True,
            "notes_never_read": True,
            "attachments_never_read": True,
        }
    )
    result["raw_fields"] = raw
    return result


def _parse_chi_library_detail(
    soup: BeautifulSoup, row: Mapping[str, Any], ledger: _ChiLedger
) -> dict[str, Any]:
    _chi_detail_shell_contract(soup, ledger=ledger)
    table = _one(
        soup.select("#contents .board-view-wrap02 > table"),
        "chi library safe detail table",
    )
    body = _one(table.find_all("tbody", recursive=False), "chi library table body")
    labels: list[str] = []
    safe: dict[str, str] = {}
    for table_row in body.find_all("tr", recursive=False):
        cells = table_row.find_all(["th", "td"], recursive=False)
        if not cells or len(cells) % 2:
            raise BusanSeoguContractError("chi library detail row shape changed")
        for index in range(0, len(cells), 2):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise BusanSeoguContractError("chi library label/value changed")
            label = _text(cells[index])
            labels.append(label)
            if label in _CHI_LIBRARY_SAFE_LABELS:
                safe[label] = _text(cells[index + 1])
            elif label not in _CHI_LIBRARY_DETAIL_LABELS:
                raise BusanSeoguContractError(
                    "unknown chi library detail field at PII boundary"
                )
    if tuple(labels) != _CHI_LIBRARY_DETAIL_LABELS:
        raise BusanSeoguContractError("chi library detail labels changed")
    if set(safe) != _CHI_LIBRARY_SAFE_LABELS:
        raise BusanSeoguContractError("chi library safe fields changed")
    for label, value in safe.items():
        if label not in {"교육주기", "교육대상"} and not value:
            raise BusanSeoguContractError(
                f"chi library safe detail {label} is empty"
            )
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    _validate_chi_detail_form(soup, ledger=ledger, identity=identity)
    if safe["강좌명"] != _clean(row.get("title")):
        raise BusanSeoguContractError("chi library list/detail title differs")
    start, end = _strict_detail_date_pair(
        safe["학습기간"], "chi library detail education period"
    )
    apply_start, apply_end = _strict_detail_date_pair(
        safe["접수기간"], "chi library detail application period"
    )
    if (start, end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        raise BusanSeoguContractError("chi library list/detail dates differ")
    source_status = safe["상태"]
    if (
        source_status not in _CHI_STATUS_MAP
        or _CHI_STATUS_MAP[source_status] != row.get("status")
    ):
        raise BusanSeoguContractError("chi library list/detail status differs")
    if row.get("status") != "CLOSED":
        raise BusanSeoguContractError(
            "chi library non-closed application contract requires review"
        )
    capacity = _CAPACITY_RE.fullmatch(safe["교육정원/대기정원"])
    if not capacity:
        raise BusanSeoguContractError("chi library capacity changed")
    result = dict(row)
    result.update(
        {
            "branch": safe["학습기관"],
            "branch_code": f"bsseogu-library-{identity}",
            "venue_name": safe["교육장소"],
            "target": safe["교육대상"],
            "fee": safe["수강료"],
            "schedule_raw": safe["교육시간"],
            "capacity_total": int(capacity.group(1)),
            "capacity_waiting": int(capacity.group(2)),
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": source_status,
            "source_institution": safe["학습기관"],
            "source_venue": safe["교육장소"],
            "source_schedule": safe["교육시간"],
            "source_fee": safe["수강료"],
            "source_education_method": safe["교육방법"],
            "inquiry_phone_value_never_read": True,
            "free_form_detail_never_read": True,
            "attachments_never_read": True,
        }
    )
    result["raw_fields"] = raw
    return result


def _city_list_contract(
    soup: BeautifulSoup, *, page: int
) -> tuple[int, Optional[Tag]]:
    if _text(_one(soup.select("title"), "city list title")) != _CITY_LIST_TITLE:
        raise BusanSeoguContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm"), "Busan city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanSeoguContractError("Busan city search form changed")
    page_field = _one(
        form.select("input[name='curPage']"), "Busan city curPage field"
    )
    if _clean(page_field.get("value")) != str(page):
        raise BusanSeoguContractError("Busan city form page differs from request")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_SEOGU_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanSeoguContractError(
                f"Busan city {name} owner filter changed"
            )

    end_link = _one(
        soup.select("div.paginate > a.pgEnd[href]"), "city last page"
    )
    end_url = urljoin(BUSAN_CITY_SEOGU_URL, _clean(end_link.get("href")))
    parsed = urlparse(end_url)
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
        or _query_single(query, "srchGugun") != BUSAN_CITY_SEOGU_GUGUN
        or _query_single(query, "srchResveInsttCd")
        != BUSAN_CITY_RESIDENT_OFFICE
    ):
        raise BusanSeoguContractError("unsafe Busan city last-page control")
    last_raw = _query_single(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanSeoguContractError("invalid Busan city last page")
    last_page = int(last_raw)

    roots = soup.select("ul.reserveList")
    if page <= last_page:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last_page + 1:
        if roots:
            raise BusanSeoguContractError(
                "Busan city sentinel unexpectedly retained a reserve list"
            )
        empty = _one(
            soup.select("div.reserveListWrap > div.txtCenter"),
            "Busan city empty marker",
        )
        if _text(empty) != "등록된 강좌가 없습니다.":
            raise BusanSeoguContractError("Busan city empty marker changed")
        root = None
    else:
        raise BusanSeoguContractError("Busan city page passed sentinel boundary")
    return last_page, root


def _city_card_date_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanSeoguContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (
            date.fromisoformat(item) for item in match.groups()
        )
    except ValueError as exc:
        raise BusanSeoguContractError(f"{label} contains an invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanSeoguContractError(f"{label} is reversed")
    return (
        apply_start.isoformat(),
        apply_end.isoformat(),
        start.isoformat(),
        end.isoformat(),
    )


def _parse_city_list_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last_page, root = _city_list_contract(soup, page=page)
    if expected_last is not None and last_page != expected_last:
        raise BusanSeoguContractError(
            f"Busan city page {page}: displayed last page changed"
        )
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"),
            "Busan city course link",
        )
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanSeoguContractError(
                f"Busan city page {page} row {position}: identity action changed"
            )
        group_id, program_id = action.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city title")
        title = _text(title_node)
        title_attr = _clean(title_node.get("title"))
        if not title or not title_attr or not title.endswith(title_attr):
            raise BusanSeoguContractError(
                f"Busan city page {page} row {position}: title changed"
            )
        status_node = _one(link.select(":scope .statusMark"), "Busan city status")
        source_status = _text(status_node)
        if source_status not in _CITY_STATUS_MAP:
            raise BusanSeoguContractError(
                f"Busan city page {page} row {position}: unknown status"
            )

        values_root = _one(link.select(":scope .infoBox > dl"), "Busan city values")
        headings = values_root.find_all("dt", recursive=False)
        values = values_root.find_all("dd", recursive=False)
        labels = tuple(_text(heading) for heading in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(headings):
            raise BusanSeoguContractError(
                f"Busan city page {page} row {position}: card labels changed"
            )
        # 문의 is the final pair.  Its value is structurally skipped.
        safe = {
            label: _text(value)
            for label, value in zip(labels[:-1], values[:-1])
        }
        if any(not value for value in safe.values()):
            raise BusanSeoguContractError(
                f"Busan city page {page} row {position}: safe value is empty"
            )
        branch = safe["기관"]
        if not branch.startswith("서구 ") or not branch.endswith("주민자치회"):
            raise BusanSeoguContractError(
                f"Busan city page {page} row {position}: course left Seo-gu owner"
            )
        apply_start, apply_end, start, end = _city_card_date_ranges(
            safe["일자"], f"Busan city page {page} row {position} dates"
        )
        raw_url = busan_seogu_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_SEOGU_PROVIDER,
                "provider_course_id": f"reserve:{group_id}:{program_id}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"reserve-{group_id}",
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
                "municipality_code": BUSAN_SEOGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_SEOGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "서구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "parser": BUSAN_SEOGU_PARSER,
                    "source_catalog": "busan_reserve_seogu_resident_centres",
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
                    "service_family": "education",
                },
            }
        )
    return rows, last_page


def _city_detail_shell_contract(soup: BeautifulSoup) -> None:
    if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
        raise BusanSeoguContractError("Busan city detail title changed")
    _one(soup.select("form#viewForm"), "Busan city detail form")
    _one(
        soup.select("form#viewForm div.reserveStateInfo"),
        "Busan city detail values",
    )


def _city_detail_dates(value: Any, label: str) -> tuple[str, str]:
    values = _CITY_DETAIL_DATE_RE.findall(_clean(value))
    if len(values) != 2:
        raise BusanSeoguContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(item) for item in values)
    except ValueError as exc:
        raise BusanSeoguContractError(f"{label} contains an invalid date") from exc
    if end < start:
        raise BusanSeoguContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _parse_city_detail(
    soup: BeautifulSoup, row: Mapping[str, Any]
) -> dict[str, Any]:
    _city_detail_shell_contract(soup)
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(
        form.get("action")
    ):
        raise BusanSeoguContractError("Busan city detail form changed")
    raw = dict(row.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    for name, expected in (
        ("resveGroupSn", group_id),
        ("progrmSn", program_id),
    ):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSeoguContractError("Busan city detail identity changed")

    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city heading")
    status_node = _one(heading.select(":scope .statusMark"), "city detail status")
    source_status = _text(status_node)
    direct_title = _clean(
        " ".join(
            _clean(child)
            for child in heading.children
            if isinstance(child, NavigableString) and _clean(child)
        )
    )
    if direct_title != _clean(row.get("title")):
        raise BusanSeoguContractError("Busan city list/detail title differs")
    if source_status != _clean(raw.get("source_status")):
        raise BusanSeoguContractError("Busan city list/detail status differs")

    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    definitions = info.find_all("dl", recursive=False)
    labels: list[str] = []
    values: list[Tag] = []
    for definition in definitions:
        labels.append(
            _text(_one(definition.find_all("dt", recursive=False), "city dt"))
        )
        values.append(
            _one(definition.find_all("dd", recursive=False), "city dd")
        )
    if tuple(labels) != _CITY_DETAIL_LABELS or len(values) != len(labels):
        raise BusanSeoguContractError("Busan city detail labels changed")
    safe = {
        label: _text(value)
        for label, value in zip(labels, values)
        if label in _CITY_SAFE_DETAIL_LABELS
    }
    if set(safe) != _CITY_SAFE_DETAIL_LABELS or any(
        not value for value in safe.values()
    ):
        raise BusanSeoguContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanSeoguContractError("Busan city free-form boundary changed")

    start, end = _city_detail_dates(
        safe["운영기간"], "Busan city operating period"
    )
    apply_start, apply_end = _city_detail_dates(
        safe["신청기간"], "Busan city application period"
    )
    if (start, end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    ):
        raise BusanSeoguContractError("Busan city list/detail dates differ")
    if _city_application_method_parts(
        safe["신청방법"], "Busan city detail application method"
    ) != _city_application_method_parts(
        raw.get("source_application_method"),
        "Busan city list application method",
    ):
        raise BusanSeoguContractError("Busan city list/detail method differs")
    if safe["운영기관"] != _clean(row.get("branch")):
        raise BusanSeoguContractError("Busan city list/detail owner differs")
    if safe["대상"] != _clean(row.get("target")):
        raise BusanSeoguContractError("Busan city list/detail target differs")

    control_root = _one(
        info.select(":scope > div.reserveBtnWrap"),
        "Busan city application/status control root",
    )
    controls = control_root.select(":scope > a.btnTypeXL")
    method = safe["신청방법"]
    normalized_status = _CITY_STATUS_MAP[source_status]
    application_url = ""
    application_type = "INFO_ONLY"
    reservation_available = False
    control_label = ""
    if normalized_status == "OPEN" and "온라인" in method:
        control = _one(controls, "open Busan city online control")
        control_label = _text(control)
        if not any(token in control_label for token in ("신청", "예약")):
            raise BusanSeoguContractError("Busan city online control changed")
        application_url = _clean(row.get("raw_url"))
        application_type = "ONLINE_RESERVATION"
        reservation_available = True
    elif normalized_status == "OPEN" and any(
        token in method for token in ("방문", "전화")
    ):
        if controls:
            raise BusanSeoguContractError(
                "offline-only Busan city course exposed an online control"
            )
        application_type = "OFFLINE_APPLY"
    elif normalized_status == "OPEN":
        raise BusanSeoguContractError(
            "open Busan city course has an unknown application method"
        )
    elif normalized_status == "CLOSED":
        control = _one(controls, "closed Busan city status control")
        control_label = _text(control)
        if control_label != "접수마감":
            raise BusanSeoguContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED":
        control = _one(controls, "scheduled Busan city status control")
        control_label = _text(control)
        if control_label not in {"대기중", "접수대기"}:
            raise BusanSeoguContractError("scheduled Busan city control changed")

    result = dict(row)
    result.update(
        {
            "application_url": application_url,
            "application_type": application_type,
            "reservation_available": reservation_available,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
        }
    )
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": source_status,
            "detail_operating_period": safe["운영기간"],
            "detail_application_period": safe["신청기간"],
            "detail_cancellation": safe["취소여부"],
            "detail_application_method": method,
            "detail_fee": safe["수강료"],
            "detail_schedule": safe["요일 /시간"],
            "detail_application_control": control_label,
            "inquiry_phone_value_never_read": True,
            "free_form_detail_never_read": True,
            "location_address_value_never_read": True,
            "instructor_value_never_read": True,
        }
    )
    result["raw_fields"] = raw
    return result


def _page_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = []
    for row in rows:
        raw = row.get("raw_fields", {})
        values.append(
            "\x1f".join(
                (
                    _clean(row.get("provider_course_id")),
                    _clean(row.get("title")),
                    _clean(row.get("start_date")),
                    _clean(row.get("end_date")),
                    _clean(raw.get("source_status")),
                )
            )
        )
    return hashlib.sha256("\x1e".join(values).encode("utf-8")).hexdigest()


def _base_meta() -> dict[str, Any]:
    return {
        "parser": BUSAN_SEOGU_PARSER,
        "canonical_url": BUSAN_SEOGU_URL,
        "canonical_list_url": busan_seogu_list_url(1),
        "companion_resident_url": BUSAN_CITY_SEOGU_URL,
        "ownership_scope": BUSAN_SEOGU_OWNERSHIP_SCOPE,
        "owner_boundary_audit": BUSAN_SEOGU_OWNER_BOUNDARY_AUDIT,
        "discovery_audit": BUSAN_SEOGU_DISCOVERY_AUDIT,
        "candidate_ids": dict(BUSAN_SEOGU_CANDIDATE_IDS),
        "pages": 0,
        "data_pages": 0,
        "district_data_pages": 0,
        "chi_data_pages": 0,
        "women_data_pages": 0,
        "happy_data_pages": 0,
        "library_data_pages": 0,
        "city_data_pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_pages": 0,
        "source_rows": 0,
        "district_source_rows": 0,
        "chi_source_rows": 0,
        "women_source_rows": 0,
        "happy_source_rows": 0,
        "library_source_rows": 0,
        "city_source_rows": 0,
        "current_source_count": 0,
        "district_current_count": 0,
        "chi_current_count": 0,
        "women_current_count": 0,
        "happy_current_count": 0,
        "library_current_count": 0,
        "city_current_count": 0,
        "expired_count": 0,
        "page_counts": {},
        "district_page_counts": {},
        "chi_page_counts": {},
        "city_page_counts": {},
        "source_status_counts": {},
        "current_status_counts": {},
        "current_category_counts": {},
        "current_facility_counts": {},
        "current_detail_ids": [],
        "application_control_count": 0,
        "offline_application_count": 0,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
    }


def collect_busan_seogu_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 250,
    detail_limit: int = 75,
    max_requests: int = 300,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_SEOGU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one atomic current/future snapshot of all five ledgers."""

    meta = _base_meta()
    if not is_busan_seogu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Busan Seo-gu education home"
        )
        return [], BUSAN_SEOGU_PARSER, meta
    try:
        limits = (timeout, max_pages, detail_limit, max_requests, max_workers)
        if any(isinstance(value, bool) for value in limits):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(
            max(1, int(max_workers)), BUSAN_SEOGU_MAX_WORKERS
        )
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_SEOGU_PARSER, meta
    if page_cap < 1 or request_cap < 1:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            "page/detail/request caps do not allow discovery"
        )
        return [], BUSAN_SEOGU_PARSER, meta

    fetch = fetcher or _default_fetcher
    factory = session_factory or _default_session_factory
    budget = _RequestBudget(request_cap)

    def add_fetch(result: _FetchResult) -> None:
        meta["network_retry_count"] += result.retries
        meta["sessions_created"] += result.sessions
        meta["network_requests"] = budget.count

    first = _fetch_many(
        (("district-first", busan_seogu_list_url(1)),),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=1,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(first)
    meta["list_requests"] += len(first.values)
    if first.errors or "district-first" not in first.values:
        meta["configured_collection_error"] = (
            "; ".join(first.errors) or "missing district first page"
        )
        return [], BUSAN_SEOGU_PARSER, meta
    try:
        first_rows, declared_total, district_last = _parse_local_list_page(
            first.values["district-first"], page=1
        )
        if district_last > page_cap:
            raise BusanSeoguContractError(
                f"max_pages cap allows {page_cap} of {district_last} "
                "district data pages"
            )
        expected_first = min(BUSAN_SEOGU_PAGE_SIZE, declared_total)
        if len(first_rows) != expected_first:
            raise BusanSeoguContractError(
                "district first-page row count differs from declared total"
            )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"district first page: {_clean(exc)}"
        return [], BUSAN_SEOGU_PARSER, meta

    district_remaining = _fetch_many(
        tuple(
            (f"district-page-{page}", busan_seogu_list_url(page))
            for page in range(2, district_last + 2)
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(district_remaining)
    meta["list_requests"] += len(district_remaining.values)
    if district_remaining.errors:
        meta["source_cap_reached"] = any(
            "max_requests" in item for item in district_remaining.errors
        )
        meta["configured_collection_error"] = "; ".join(
            district_remaining.errors
        )
        return [], BUSAN_SEOGU_PARSER, meta

    district_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    district_counts: dict[int, int] = {1: len(first_rows)}
    try:
        for page in range(2, district_last + 1):
            key = f"district-page-{page}"
            soup = district_remaining.values.get(key)
            if soup is None:
                raise BusanSeoguContractError(
                    f"district page {page} response is missing"
                )
            rows, _, _ = _parse_local_list_page(
                soup, page=page, expected_total=declared_total
            )
            expected = (
                BUSAN_SEOGU_PAGE_SIZE
                if page < district_last
                else declared_total
                - BUSAN_SEOGU_PAGE_SIZE * (district_last - 1)
            )
            if len(rows) != expected:
                raise BusanSeoguContractError(
                    f"district page {page} row count mismatch"
                )
            district_pages[page] = rows
            district_counts[page] = len(rows)
        sentinel_page = district_last + 1
        sentinel = district_remaining.values.get(
            f"district-page-{sentinel_page}"
        )
        if sentinel is None:
            raise BusanSeoguContractError("district sentinel response is missing")
        sentinel_rows, _, _ = _parse_local_list_page(
            sentinel, page=sentinel_page, expected_total=declared_total
        )
        if sentinel_rows:
            raise BusanSeoguContractError(
                "district immediate post-final page is not empty"
            )
        meta["sentinel_requests"] += 1
        district_listed = [
            row
            for page in range(1, district_last + 1)
            for row in district_pages[page]
        ]
        if len(district_listed) != declared_total:
            raise BusanSeoguContractError(
                "district declared total and traversed rows differ"
            )
        identities = [row["provider_course_id"] for row in district_listed]
        if len(identities) != len(set(identities)):
            raise BusanSeoguContractError(
                "duplicate el_code identities in complete district archive"
            )
    except Exception as exc:
        meta["configured_collection_error"] = f"district ledger: {_clean(exc)}"
        return [], BUSAN_SEOGU_PARSER, meta

    chi_pages: dict[str, dict[int, list[dict[str, Any]]]] = {}
    chi_counts: dict[str, dict[int, int]] = {}
    chi_totals: dict[str, int] = {}
    chi_last_pages: dict[str, int] = {}
    chi_listed_by_key: dict[str, list[dict[str, Any]]] = {}
    for ledger in _CHI_LEDGERS:
        first_key = f"chi-{ledger.key}-first"
        child_first = _fetch_many(
            ((first_key, busan_seogu_chi_list_url(ledger, 1)),),
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            max_workers=1,
            sleeper=sleeper,
            budget=budget,
        )
        add_fetch(child_first)
        meta["list_requests"] += len(child_first.values)
        if child_first.errors or first_key not in child_first.values:
            meta["configured_collection_error"] = (
                "; ".join(child_first.errors)
                or f"missing chi {ledger.key} first page"
            )
            return [], BUSAN_SEOGU_PARSER, meta
        try:
            child_first_rows, child_total, child_last = _parse_chi_list_page(
                child_first.values[first_key], ledger=ledger, page=1
            )
            if not child_first_rows:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} first page contains no education rows"
                )
            if district_last + sum(chi_last_pages.values()) + child_last > page_cap:
                needed = district_last + sum(chi_last_pages.values()) + child_last
                raise BusanSeoguContractError(
                    f"max_pages cap allows {page_cap} of at least {needed} "
                    "combined data pages"
                )
            expected_first = min(_CHI_PAGE_SIZE, child_total)
            if len(child_first_rows) != expected_first:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} first-page row count differs from total"
                )
        except Exception as exc:
            meta["source_cap_reached"] = "cap" in _clean(exc)
            meta["configured_collection_error"] = (
                f"chi {ledger.key} first page: {_clean(exc)}"
            )
            return [], BUSAN_SEOGU_PARSER, meta

        child_remaining = _fetch_many(
            tuple(
                (
                    f"chi-{ledger.key}-page-{page}",
                    busan_seogu_chi_list_url(ledger, page),
                )
                for page in range(2, child_last + 2)
            ),
            fetcher=fetch,
            session_factory=factory,
            timeout=request_timeout,
            max_workers=workers,
            sleeper=sleeper,
            budget=budget,
        )
        add_fetch(child_remaining)
        meta["list_requests"] += len(child_remaining.values)
        if child_remaining.errors:
            meta["source_cap_reached"] = any(
                "max_requests" in item for item in child_remaining.errors
            )
            meta["configured_collection_error"] = "; ".join(
                child_remaining.errors
            )
            return [], BUSAN_SEOGU_PARSER, meta

        current_pages: dict[int, list[dict[str, Any]]] = {
            1: child_first_rows
        }
        current_counts: dict[int, int] = {1: len(child_first_rows)}
        try:
            for page in range(2, child_last + 1):
                key = f"chi-{ledger.key}-page-{page}"
                soup = child_remaining.values.get(key)
                if soup is None:
                    raise BusanSeoguContractError(
                        f"chi {ledger.key} page {page} response is missing"
                    )
                rows, _, _ = _parse_chi_list_page(
                    soup,
                    ledger=ledger,
                    page=page,
                    expected_total=child_total,
                )
                expected = (
                    _CHI_PAGE_SIZE
                    if page < child_last
                    else child_total - _CHI_PAGE_SIZE * (child_last - 1)
                )
                if len(rows) != expected:
                    raise BusanSeoguContractError(
                        f"chi {ledger.key} page {page} row count mismatch"
                    )
                current_pages[page] = rows
                current_counts[page] = len(rows)
            sentinel_page = child_last + 1
            sentinel = child_remaining.values.get(
                f"chi-{ledger.key}-page-{sentinel_page}"
            )
            if sentinel is None:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} sentinel response is missing"
                )
            sentinel_rows, _, _ = _parse_chi_list_page(
                sentinel,
                ledger=ledger,
                page=sentinel_page,
                expected_total=child_total,
            )
            if sentinel_rows:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} post-final page is not empty"
                )
            meta["sentinel_requests"] += 1
            child_listed = [
                row
                for page in range(1, child_last + 1)
                for row in current_pages[page]
            ]
            if len(child_listed) != child_total:
                raise BusanSeoguContractError(
                    f"chi {ledger.key} total and traversed rows differ"
                )
            identities = [row["provider_course_id"] for row in child_listed]
            if len(identities) != len(set(identities)):
                raise BusanSeoguContractError(
                    f"duplicate identities in chi {ledger.key} ledger"
                )
        except Exception as exc:
            meta["configured_collection_error"] = (
                f"chi {ledger.key} ledger: {_clean(exc)}"
            )
            return [], BUSAN_SEOGU_PARSER, meta
        chi_pages[ledger.key] = current_pages
        chi_counts[ledger.key] = current_counts
        chi_totals[ledger.key] = child_total
        chi_last_pages[ledger.key] = child_last
        chi_listed_by_key[ledger.key] = child_listed

    city_first = _fetch_many(
        (("city-first", busan_seogu_city_list_url(1)),),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=1,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(city_first)
    meta["list_requests"] += len(city_first.values)
    if city_first.errors or "city-first" not in city_first.values:
        meta["configured_collection_error"] = (
            "; ".join(city_first.errors) or "missing Busan city first page"
        )
        return [], BUSAN_SEOGU_PARSER, meta
    try:
        city_first_rows, city_last = _parse_city_list_page(
            city_first.values["city-first"], page=1
        )
        if not city_first_rows:
            raise BusanSeoguContractError(
                "Busan city Seo-gu first page contains no resident courses"
            )
        combined_data_pages = (
            district_last + sum(chi_last_pages.values()) + city_last
        )
        if combined_data_pages > page_cap:
            raise BusanSeoguContractError(
                f"max_pages cap allows {page_cap} of {combined_data_pages} "
                "combined data pages"
            )
        required_list_requests = (
            district_last
            + 3
            + sum(last + 3 for last in chi_last_pages.values())
            + city_last
            + 3
        )
        meta.update(
            {
                "data_pages": combined_data_pages,
                "district_data_pages": district_last,
                "chi_data_pages": sum(chi_last_pages.values()),
                "women_data_pages": chi_last_pages["women"],
                "happy_data_pages": chi_last_pages["happy"],
                "library_data_pages": chi_last_pages["library"],
                "city_data_pages": city_last,
                "required_list_requests": required_list_requests,
            }
        )
    except Exception as exc:
        meta["source_cap_reached"] = "cap" in _clean(exc)
        meta["configured_collection_error"] = f"Busan city first page: {_clean(exc)}"
        return [], BUSAN_SEOGU_PARSER, meta

    city_remaining = _fetch_many(
        tuple(
            (f"city-page-{page}", busan_seogu_city_list_url(page))
            for page in range(2, city_last + 2)
        ),
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(city_remaining)
    meta["list_requests"] += len(city_remaining.values)
    if city_remaining.errors:
        meta["source_cap_reached"] = any(
            "max_requests" in item for item in city_remaining.errors
        )
        meta["configured_collection_error"] = "; ".join(city_remaining.errors)
        return [], BUSAN_SEOGU_PARSER, meta

    city_pages: dict[int, list[dict[str, Any]]] = {1: city_first_rows}
    city_counts: dict[int, int] = {1: len(city_first_rows)}
    try:
        for page in range(2, city_last + 1):
            soup = city_remaining.values.get(f"city-page-{page}")
            if soup is None:
                raise BusanSeoguContractError(
                    f"Busan city page {page} response is missing"
                )
            rows, _ = _parse_city_list_page(
                soup, page=page, expected_last=city_last
            )
            if page < city_last and len(rows) != 10:
                raise BusanSeoguContractError(
                    f"Busan city page {page} is not full"
                )
            if page == city_last and not 1 <= len(rows) <= 10:
                raise BusanSeoguContractError(
                    "Busan city final-page row count is invalid"
                )
            city_pages[page] = rows
            city_counts[page] = len(rows)
        sentinel_page = city_last + 1
        sentinel = city_remaining.values.get(f"city-page-{sentinel_page}")
        if sentinel is None:
            raise BusanSeoguContractError("Busan city sentinel response is missing")
        sentinel_rows, _ = _parse_city_list_page(
            sentinel, page=sentinel_page, expected_last=city_last
        )
        if sentinel_rows:
            raise BusanSeoguContractError(
                "Busan city immediate post-final page is not empty"
            )
        meta["sentinel_requests"] += 1
        city_listed = [
            row
            for page in range(1, city_last + 1)
            for row in city_pages[page]
        ]
        city_ids = [row["provider_course_id"] for row in city_listed]
        if len(city_ids) != len(set(city_ids)):
            raise BusanSeoguContractError(
                "duplicate identities in Busan city Seo-gu ledger"
            )
    except Exception as exc:
        meta["configured_collection_error"] = f"Busan city ledger: {_clean(exc)}"
        return [], BUSAN_SEOGU_PARSER, meta

    chi_listed = [
        row
        for ledger in _CHI_LEDGERS
        for row in chi_listed_by_key[ledger.key]
    ]
    listed = district_listed + chi_listed + city_listed
    all_ids = [row["provider_course_id"] for row in listed]
    if len(all_ids) != len(set(all_ids)):
        meta["configured_collection_error"] = (
            "duplicate identities across the five Seo-gu official ledgers"
        )
        return [], BUSAN_SEOGU_PARSER, meta
    try:
        current: list[dict[str, Any]] = []
        for row in listed:
            end_value = _clean(row.get("end_date"))
            if not end_value:
                raw = row.get("raw_fields", {})
                start_value = date.fromisoformat(_clean(row.get("start_date")))
                if (
                    raw.get("audited_expired_without_end") is True
                    and row.get("status") == "CLOSED"
                    and start_value < cutoff
                ):
                    continue
                raise BusanSeoguContractError(
                    "unaudited row has no education end date"
                )
            if date.fromisoformat(end_value) >= cutoff:
                current.append(row)
    except (TypeError, ValueError, BusanSeoguContractError) as exc:
        meta["configured_collection_error"] = (
            f"current/future boundary: {_clean(exc)}"
        )
        return [], BUSAN_SEOGU_PARSER, meta
    district_current = [
        row
        for row in current
        if row["raw_fields"]["source_catalog"]
        == "busan_seogu_lifelong_education"
    ]
    city_current = [
        row
        for row in current
        if row["raw_fields"]["source_catalog"]
        == "busan_reserve_seogu_resident_centres"
    ]
    chi_current_by_key = {
        ledger.key: [
            row
            for row in current
            if row["raw_fields"]["source_catalog"]
            == f"busan_seogu_chi_{ledger.key}"
        ]
        for ledger in _CHI_LEDGERS
    }
    meta.update(
        {
            "source_rows": len(listed),
            "district_source_rows": len(district_listed),
            "chi_source_rows": len(chi_listed),
            "women_source_rows": len(chi_listed_by_key["women"]),
            "happy_source_rows": len(chi_listed_by_key["happy"]),
            "library_source_rows": len(chi_listed_by_key["library"]),
            "city_source_rows": len(city_listed),
            "current_source_count": len(current),
            "district_current_count": len(district_current),
            "chi_current_count": sum(
                len(values) for values in chi_current_by_key.values()
            ),
            "women_current_count": len(chi_current_by_key["women"]),
            "happy_current_count": len(chi_current_by_key["happy"]),
            "library_current_count": len(chi_current_by_key["library"]),
            "city_current_count": len(city_current),
            "expired_count": len(listed) - len(current),
            "page_counts": {
                "district": district_counts,
                "women": chi_counts["women"],
                "happy": chi_counts["happy"],
                "library": chi_counts["library"],
                "city": city_counts,
            },
            "district_page_counts": district_counts,
            "chi_page_counts": chi_counts,
            "city_page_counts": city_counts,
            "source_status_counts": dict(
                Counter(row["raw_fields"]["source_status"] for row in listed)
            ),
            "current_status_counts": dict(
                Counter(row["raw_fields"]["source_status"] for row in current)
            ),
            "current_category_counts": dict(
                Counter(row["category"] for row in current)
            ),
        }
    )
    if len(current) > detail_cap:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"detail_limit cap allows {detail_cap} of {len(current)} current details"
        )
        return [], BUSAN_SEOGU_PARSER, meta
    stability_count = 2 * (2 + len(_CHI_LEDGERS))
    if budget.count + len(current) + stability_count > request_cap:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = (
            f"max_requests cap cannot cover {len(current)} details and "
            f"{stability_count} stability rechecks"
        )
        return [], BUSAN_SEOGU_PARSER, meta

    detail_jobs = tuple(
        (
            (
                row["raw_fields"]["source_catalog"],
                row["provider_course_id"],
            ),
            row["raw_url"],
        )
        for row in current
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
    add_fetch(details)
    if details.errors:
        meta["source_cap_reached"] = any(
            "max_requests" in item for item in details.errors
        )
        meta["configured_collection_error"] = "; ".join(details.errors)
        return [], BUSAN_SEOGU_PARSER, meta
    verified: list[dict[str, Any]] = []
    try:
        for row in current:
            source = row["raw_fields"]["source_catalog"]
            identity = row["provider_course_id"]
            soup = details.values.get((source, identity))
            if soup is None:
                raise BusanSeoguContractError(
                    f"detail {identity} response is missing"
                )
            if source == "busan_seogu_lifelong_education":
                verified.append(_parse_local_detail(soup, row))
            elif source == "busan_seogu_chi_women":
                verified.append(
                    _parse_chi_women_detail(soup, row, _CHI_LEDGERS[0])
                )
            elif source == "busan_seogu_chi_happy":
                verified.append(
                    _parse_chi_happy_detail(soup, row, _CHI_LEDGERS[1])
                )
            elif source == "busan_seogu_chi_library":
                verified.append(
                    _parse_chi_library_detail(soup, row, _CHI_LEDGERS[2])
                )
            elif source == "busan_reserve_seogu_resident_centres":
                verified.append(_parse_city_detail(soup, row))
            else:
                raise BusanSeoguContractError("unknown source catalogue")
    except Exception as exc:
        meta["configured_collection_error"] = f"detail contract: {_clean(exc)}"
        return [], BUSAN_SEOGU_PARSER, meta
    meta["detail_pages"] = len(verified)

    recheck_jobs: tuple[tuple[Any, str], ...] = tuple(
        [
            ("district-recheck-first", busan_seogu_list_url(1)),
            ("district-recheck-last", busan_seogu_list_url(district_last)),
        ]
        + [
            (
                f"chi-{ledger.key}-recheck-{boundary}",
                busan_seogu_chi_list_url(
                    ledger,
                    1 if boundary == "first" else chi_last_pages[ledger.key],
                ),
            )
            for ledger in _CHI_LEDGERS
            for boundary in ("first", "last")
        ]
        + [
            ("city-recheck-first", busan_seogu_city_list_url(1)),
            ("city-recheck-last", busan_seogu_city_list_url(city_last)),
        ]
    )
    rechecks = _fetch_many(
        recheck_jobs,
        fetcher=fetch,
        session_factory=factory,
        timeout=request_timeout,
        max_workers=workers,
        sleeper=sleeper,
        budget=budget,
    )
    add_fetch(rechecks)
    meta["list_requests"] += len(rechecks.values)
    if rechecks.errors:
        meta["configured_collection_error"] = "; ".join(rechecks.errors)
        return [], BUSAN_SEOGU_PARSER, meta
    try:
        for key, page in (
            ("district-recheck-first", 1),
            ("district-recheck-last", district_last),
        ):
            rows, _, _ = _parse_local_list_page(
                rechecks.values[key],
                page=page,
                expected_total=declared_total,
            )
            if _page_signature(rows) != _page_signature(district_pages[page]):
                raise BusanSeoguContractError(
                    f"district boundary page {page} changed"
                )
        for ledger in _CHI_LEDGERS:
            for boundary, page in (
                ("first", 1),
                ("last", chi_last_pages[ledger.key]),
            ):
                key = f"chi-{ledger.key}-recheck-{boundary}"
                rows, _, _ = _parse_chi_list_page(
                    rechecks.values[key],
                    ledger=ledger,
                    page=page,
                    expected_total=chi_totals[ledger.key],
                )
                if _page_signature(rows) != _page_signature(
                    chi_pages[ledger.key][page]
                ):
                    raise BusanSeoguContractError(
                        f"chi {ledger.key} boundary page {page} changed"
                    )
        for key, page in (
            ("city-recheck-first", 1),
            ("city-recheck-last", city_last),
        ):
            rows, _ = _parse_city_list_page(
                rechecks.values[key], page=page, expected_last=city_last
            )
            if _page_signature(rows) != _page_signature(city_pages[page]):
                raise BusanSeoguContractError(
                    f"Busan city boundary page {page} changed"
                )
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], BUSAN_SEOGU_PARSER, meta
    meta["stability_rechecks"] = stability_count

    if dedupe_rows is not None:
        try:
            deduped = list(dedupe_rows(list(verified)))
        except Exception as exc:
            meta["configured_collection_error"] = f"dedupe failed: {_clean(exc)}"
            return [], BUSAN_SEOGU_PARSER, meta
        before = [row["provider_course_id"] for row in verified]
        after = [row.get("provider_course_id") for row in deduped]
        if before != after:
            meta["configured_collection_error"] = (
                "dedupe changed canonical current course identities"
            )
            return [], BUSAN_SEOGU_PARSER, meta
        verified = deduped

    meta.update(
        {
            "pages": meta["list_requests"] + meta["detail_pages"],
            "current_detail_ids": [
                row["provider_course_id"] for row in verified
            ],
            "current_facility_counts": dict(
                Counter(row["branch"] for row in verified)
            ),
            "application_control_count": sum(
                bool(row.get("application_url")) for row in verified
            ),
            "offline_application_count": sum(
                row.get("application_type") == "OFFLINE_APPLY"
                for row in verified
            ),
            "network_requests": budget.count,
            "snapshot_complete": True,
            "no_current_data": not verified,
            "configured_collection_error": "",
        }
    )
    return verified, BUSAN_SEOGU_PARSER, meta


collect = collect_busan_seogu_education
is_target = is_busan_seogu_education_target
