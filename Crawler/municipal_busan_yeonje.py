"""Atomic education collector for Busan Yeonje-gu's public ledgers.

Yeonje-gu's complete municipal education snapshot is split across the
district lifelong-learning catalogue, the Yeonje resident-council partition
of Busan's integrated reservation site, and four Yeonje-owned offices on the
Busan Lifelong Learning Platform.  Platform external ``lecIdx`` rows and its
legacy list-only rows are republications of the district catalogue; only
native ``LEARNING_*`` rows are additionally published by this owner.

Every data page, the immediate post-final sentinel, stable boundary pages (or
two equal complete platform censuses), and every current/future safe detail
are mandatory.  A change in identity, pagination, ownership, or a safe detail
contract discards the whole union.  Applicant rows, application forms,
account pages, instructor/contact values, attachments and free-form detail
payloads are never read or fetched.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
import hashlib
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong


BUSAN_YEONJE_PROVIDER = "MUNI_WWW_YEONJE_GO_KR_73BA35A2"
BUSAN_YEONJE_CANONICAL_ALIAS_PROVIDER = "MUNI_WWW_YEONJE_GO_KR_6CA2C4DE"
BUSAN_YEONJE_MAIN_ALIAS_PROVIDER = "MUNI_WWW_YEONJE_GO_KR_F65C89B4"
BUSAN_CITY_YEONJE_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_6976F0A8"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_YEONJE_MUNICIPALITY_CODE = "2647000000"
BUSAN_YEONJE_MUNICIPALITY_NAME = "부산광역시 연제구"
BUSAN_YEONJE_HOST = "www.yeonje.go.kr"
BUSAN_YEONJE_LIST_PATH = "/edu/lecture/list.do"
BUSAN_YEONJE_DETAIL_PATH = "/edu/lecture/view.do"
BUSAN_YEONJE_APPLY_PATH = "/edu/lecture/write.do"
BUSAN_YEONJE_MENU = "0701010000"
BUSAN_YEONJE_EXTERNAL_MENU = "0701000000"
BUSAN_YEONJE_REGISTERED_URL = (
    f"https://{BUSAN_YEONJE_HOST}/edu/contents.do?"
    + urlencode({"mId": BUSAN_YEONJE_MENU})
)
BUSAN_YEONJE_CANONICAL_URL = (
    f"https://{BUSAN_YEONJE_HOST}{BUSAN_YEONJE_LIST_PATH}?"
    + urlencode({"mId": BUSAN_YEONJE_MENU})
)
BUSAN_YEONJE_URL = BUSAN_YEONJE_CANONICAL_URL
BUSAN_YEONJE_PAGE_SIZE = 10

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_YEONJE_GUGUN = "13"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_YEONJE_CANDIDATE_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    "progrmSn=&resveGroupSn=&srchGugun=13&srchResveInsttCd=33"
)
BUSAN_CITY_YEONJE_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_YEONJE_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_PAGE_SIZE = 1000
BUSAN_LIFELONG_YEONJE_OFFICES: tuple[tuple[str, str], ...] = (
    ("OFFICE_00002670", "연제구청"),
    ("OFFICE_00002760", "거제1동 행정복지센터"),
    ("OFFICE_00002910", "거제2동 행정복지센터"),
    ("OFFICE_00002770", "거제4동 행정복지센터"),
)
# The parent integration changes these four shared offices to the dedicated
# ownership token below.  Keeping this one explicit constant makes that
# hand-off auditable and prevents a silent double owner after promotion.
BUSAN_LIFELONG_EXPECTED_OWNERSHIP = "duplicate_dedicated_yeonje_owner"
BUSAN_LIFELONG_DEDICATED_OWNERSHIP = "duplicate_dedicated_yeonje_owner"

BUSAN_YEONJE_FETCH_ATTEMPTS = 3
BUSAN_YEONJE_MAX_WORKERS = 12
BUSAN_YEONJE_MAX_HTML_BYTES = 12_000_000
BUSAN_YEONJE_PARSER = (
    "yeonje_lecture_all_pages+empty_sentinel+stable_first_last+"
    "busan_lifelong_four_yeonje_offices_pageunit1000_two_complete_censuses+"
    "external_lecidx_and_listonly_duplicate_suppression+native_learning+"
    "busan_reserve_gugun13_office33_all_pages+empty_sentinel+stable_first_last+"
    "all_current_safe_details+identity_bound_apply_no_form_fetch+pii_never_read+"
    "atomic_three_ledger_snapshot"
)
BUSAN_YEONJE_OWNERSHIP_SCOPE = (
    "yeonje_complete_lifelong_catalogue_native_platform_courses_and_exact_"
    "busan_city_yeonje_resident_council_education"
)

BUSAN_YEONJE_CANDIDATE_IDS: Mapping[str, str] = {
    "registered_contents_redirect": "MUNI_IR_B24708E62D19",
    "canonical_complete_catalogue": "MUNI_IR_36E54AD8BE14",
    "district_main_alias": "MUNI_IR_42AFB37AE6EB",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "busan_resident_councils": "MUNI_IR_F3E36EF468AA",
    "separate_living_culture_facility": "MUNI_IR_B8064EE21FAE",
}

BUSAN_YEONJE_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_YEONJE_PROVIDER: {
        "decision": "retain_provider_retarget_redirect_to_complete_owner",
        "candidate_id": BUSAN_YEONJE_CANDIDATE_IDS["registered_contents_redirect"],
        "registered_url": BUSAN_YEONJE_REGISTERED_URL,
        "canonical_candidate_id": BUSAN_YEONJE_CANDIDATE_IDS[
            "canonical_complete_catalogue"
        ],
        "canonical_url": BUSAN_YEONJE_CANONICAL_URL,
        "identity_rule": "numeric lecIdx",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_exact_local_republications_keep_native_learning_ids",
        "candidate_id": BUSAN_YEONJE_CANDIDATE_IDS["busan_lifelong_federation"],
        "office_codes": tuple(code for code, _name in BUSAN_LIFELONG_YEONJE_OFFICES),
        "identity_rule": (
            "external lecIdx or unique immutable list-only key belongs to local owner; "
            "LEARNING_* remains independent"
        ),
    },
    BUSAN_CITY_YEONJE_PROVIDER: {
        "decision": "collect_exact_yeonje_resident_council_partition",
        "candidate_id": BUSAN_YEONJE_CANDIDATE_IDS["busan_resident_councils"],
        "url": BUSAN_CITY_YEONJE_CANDIDATE_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_YEONJE_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "DISTRICT_MAIN_ALIAS": {
        "decision": "exclude_navigation_alias",
        "candidate_id": BUSAN_YEONJE_CANDIDATE_IDS["district_main_alias"],
    },
    "YEONJE_LIVING_CULTURE_CENTER": {
        "decision": "exclude_separate_facility_owner",
        "candidate_id": BUSAN_YEONJE_CANDIDATE_IDS[
            "separate_living_culture_facility"
        ],
        "url": "https://www.yjccc.or.kr/?pagecode=P000000032",
        "reason": "facility-specific catalogue is not the municipal integrated owner",
    },
    "PRIVATE_BOUNDARY": {
        "decision": "never_read_or_fetch",
        "excluded": (
            "applicant rows, application forms, account/history pages, instructor "
            "and contact values, attachment names and free-form descriptions"
        ),
    },
}

BUSAN_YEONJE_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_YEONJE_CANONICAL_URL,
    "district_rows": 415,
    "district_data_pages": 42,
    "district_page_counts": {"1-41": 10, "42": 5},
    "district_sentinel_page": 43,
    "district_current_rows": 16,
    "district_status_counts": {
        "교육마감": 356,
        "접수마감": 43,
        "접수중": 6,
        "접수마감 교육중": 5,
        "접수마감 교육준비": 3,
        "접수중 교육중": 2,
    },
    "lifelong_offices": tuple(code for code, _name in BUSAN_LIFELONG_YEONJE_OFFICES),
    "lifelong_rows_by_office": {
        "OFFICE_00002670": 605,
        "OFFICE_00002760": 0,
        "OFFICE_00002910": 0,
        "OFFICE_00002770": 0,
    },
    "lifelong_rows": 605,
    "lifelong_external_rows": 366,
    "lifelong_list_only_rows": 49,
    "lifelong_external_rows_matching_local": 366,
    "lifelong_list_only_rows_matching_local": 49,
    "lifelong_native_rows": 190,
    "lifelong_native_current_rows": 77,
    "resident_url": BUSAN_CITY_YEONJE_URL,
    "resident_rows": 39,
    "resident_data_pages": 4,
    "resident_page_counts": {"1-3": 10, "4": 9},
    "resident_sentinel_page": 5,
    "resident_current_rows": 39,
    "resident_status_counts": {"접수마감": 39},
    "source_rows": 1059,
    "duplicate_platform_rows": 415,
    "unique_education_source_rows": 644,
    "atomic_current_rows": 132,
    "atomic_status_counts": {"OPEN": 15, "CLOSED": 117},
    "active_online_application_rows": 15,
    "required_list_requests": 64,
    "required_detail_requests": 132,
    "complete_network_requests": 196,
    "conclusion": (
        "collect the complete district catalogue, native platform courses and the "
        "exact resident-council partition; suppress all 415 proven republications"
    ),
}

BUSAN_YEONJE_PII_FIELDS_NEVER_READ = (
    "district instructor/contact/attachment/free-form/applicant values",
    "platform instructor/contact/enrolment/free-form values",
    "Busan city inquiry/attachment/free-form values",
    "application forms, account pages and applicant lists",
)


class BusanYeonjeContractError(ValueError):
    """Raised when one of the audited Yeonje-gu source contracts changes."""


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
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*"
    r"(\d{1,2})(?:\.)?(?!\d)"
)
_PLATFORM_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2}|\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*"
    r"(\d{1,2})(?!\d)"
)
_LOCAL_KEYSET_ID_RE = re.compile(r"lecIdx['\"]?\s*:\s*['\"]?([1-9]\d*)")
_LOCAL_KEYSET_PAGE_RE = re.compile(r"page['\"]?\s*:\s*['\"]?([1-9]\d*)")
_LOCAL_PAGE_RE = re.compile(r"^goPage\(\s*([1-9]\d*)\s*\);\s*return\s+false;?$")
_PLATFORM_INTERNAL_RE = re.compile(
    r"fn_learning_detail\(\s*['\"](LEARNING_\d{8})['\"]\s*\)"
)
_PLATFORM_PAGE_RE = re.compile(r"(?:pageIndex=|fn_list\(\s*)(\d+)")
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

_LOCAL_LIST_LABELS = (
    "접수기간",
    "모집/신청",
    "학습기간",
    "교육기관",
    "접수방법",
    "상태",
)
_LOCAL_STATUS_MAP = {
    "교육마감": "CLOSED",
    "접수마감": "CLOSED",
    "접수중": "OPEN",
    "접수마감 교육중": "CLOSED",
    "접수마감 교육준비": "CLOSED",
    "접수중 교육중": "OPEN",
}
_LOCAL_DETAIL_LABELS = (
    "사업명",
    "학습기관",
    "학습기간",
    "접수기간",
    "교육시간",
    "강사명",
    "수강료",
    "추가비용",
    "교육방법",
    "교육대상",
    "교육주기",
    "교육정원",
    "교육장소",
    "교육문의전화",
    "접수방법",
    "상태",
    "직업능력개발훈련비지원",
    "학점은행제평가(학점)인증",
    "평생학습계좌제평가인증",
    "언어",
    "시각장애지원",
    "청각장애지원",
    "신청서",
    "기타파일",
)
_LOCAL_DETAIL_SAFE_LABELS = frozenset(
    {
        "사업명",
        "학습기관",
        "학습기간",
        "접수기간",
        "교육시간",
        "수강료",
        "추가비용",
        "교육방법",
        "교육대상",
        "교육주기",
        "교육정원",
        "교육장소",
        "접수방법",
        "상태",
    }
)
_LOCAL_DETAIL_PRIVATE_LABELS = frozenset(
    {"강사명", "교육문의전화", "신청서", "기타파일"}
)
_LOCAL_DETAIL_ALLOW_BLANK = frozenset({"교육시간", "추가비용", "접수방법"})
_LOCAL_APPLICANT_HEADERS = ("번호", "이름", "연락처", "신청일", "비고")

_PLATFORM_STATUS_MAP = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "대기": "SCHEDULED",
    "대기접수": "SCHEDULED",
    "접수종료": "CLOSED",
    "마감": "CLOSED",
    "교육중": "CLOSED",
    "교육종료": "CLOSED",
    "교육완료": "CLOSED",
    "폐강": "CANCELLED",
    "": "CLOSED",
}
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
_CITY_DETAIL_SAFE_LABELS = frozenset(_CITY_DETAIL_REQUIRED_LABELS) - {"문의전화"}
_CITY_DETAIL_SKIPPED_LABELS = frozenset({"문의전화", "첨부파일"})


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _decoded(value: Any) -> str:
    current = _clean(value)
    for _ in range(2):
        current = _clean(unescape(current))
    return current


def _compact_label(value: Any) -> str:
    return "".join(_clean(value).split())


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanYeonjeContractError(
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


def _normal_path(value: str) -> str:
    return re.sub(r"/{2,}", "/", value or "/")


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("Asia/Seoul"))
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except ValueError as exc:
        raise BusanYeonjeContractError("today must be an ISO date") from exc


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanYeonjeContractError(f"{label} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanYeonjeContractError(f"{label} must be a positive integer") from exc
    if result < 1:
        raise BusanYeonjeContractError(f"{label} must be a positive integer")
    return result


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanYeonjeContractError("invalid source date") from exc
    return result


def _date_range(value: Any, label: str) -> tuple[str, str]:
    found = _dates(value)
    if len(found) != 2 or found[1] < found[0]:
        raise BusanYeonjeContractError(f"{label} changed or is reversed")
    return found[0].isoformat(), found[1].isoformat()


def _platform_dates(value: Any) -> list[date]:
    result: list[date] = []
    for year_raw, month, day in _PLATFORM_DATE_RE.findall(_clean(value)):
        year = int(year_raw)
        if year < 100:
            year += 2000
        try:
            result.append(date(year, int(month), int(day)))
        except ValueError as exc:
            raise BusanYeonjeContractError("invalid lifelong source date") from exc
    return result


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https"
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.params
        or parsed.fragment
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    return (
        f"https://{(parsed.hostname or '').rstrip('.').lower()}"
        f"{_normal_path(parsed.path)}?{urlencode(sorted((key, item) for key, values in query.items() for item in values))}"
    )


def is_busan_yeonje_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")).upper() != BUSAN_YEONJE_PROVIDER:
        return False
    value = _compare_url(_target_value(target, "url"))
    return value in {
        _compare_url(BUSAN_YEONJE_CANONICAL_URL),
        _compare_url(BUSAN_YEONJE_REGISTERED_URL),
    }


is_target = is_busan_yeonje_education_target


def busan_yeonje_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    pairs: list[tuple[str, str]] = [("mId", BUSAN_YEONJE_MENU)]
    if value > 1:
        pairs.append(("page", str(value)))
    return f"https://{BUSAN_YEONJE_HOST}{BUSAN_YEONJE_LIST_PATH}?" + urlencode(pairs)


def busan_yeonje_detail_url(identity: Any, page: int = 1) -> str:
    value = _clean(identity)
    page_value = _positive_int(page, "page")
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanYeonjeContractError("district identity is malformed")
    return f"https://{BUSAN_YEONJE_HOST}{BUSAN_YEONJE_DETAIL_PATH}?" + urlencode(
        (("mId", BUSAN_YEONJE_MENU), ("lecIdx", value), ("page", str(page_value)))
    )


def busan_yeonje_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", str(value)),
            ("srchGugun", BUSAN_CITY_YEONJE_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_yeonje_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanYeonjeContractError("city identity is malformed")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def busan_yeonje_lifelong_list_url(office_code: str, page: int = 1) -> str:
    if office_code not in {code for code, _name in BUSAN_LIFELONG_YEONJE_OFFICES}:
        raise BusanYeonjeContractError("unknown Yeonje lifelong office")
    value = _positive_int(page, "page")
    return f"https://{_lifelong.BUSAN_LIFELONG_HOST}{BUSAN_LIFELONG_LIST_PATH}?" + urlencode(
        (
            ("display_type", "2"),
            ("pageUnit", str(BUSAN_LIFELONG_PAGE_SIZE)),
            ("l_search_ch", "0"),
            ("inst_id", office_code),
            ("pageIndex", str(value)),
        )
    )


def busan_yeonje_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanYeonjeContractError("lifelong identity is malformed")
    return f"https://{_lifelong.BUSAN_LIFELONG_HOST}{BUSAN_LIFELONG_DETAIL_PATH}?" + urlencode(
        {"lng_id": value}
    )


def canonical_busan_yeonje_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONJE_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_YEONJE_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) not in ({"mId", "lecIdx"}, {"mId", "lecIdx", "page"})
        or query.get("mId") not in ([BUSAN_YEONJE_MENU], [BUSAN_YEONJE_EXTERNAL_MENU])
    ):
        return ""
    identity = _query_one(query, "lecIdx")
    if not _IDENTITY_RE.fullmatch(identity):
        return ""
    if "page" in query and not _IDENTITY_RE.fullmatch(_query_one(query, "page")):
        return ""
    return f"lecIdx:{identity}"


def _platform_offices() -> tuple[_lifelong.BusanOffice, ...]:
    result: list[_lifelong.BusanOffice] = []
    for code, name in BUSAN_LIFELONG_YEONJE_OFFICES:
        office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(code)
        if (
            office is None
            or office.name != name
            or office.municipality_code
            or office.municipality_name
            or office.ownership != BUSAN_LIFELONG_EXPECTED_OWNERSHIP
        ):
            raise BusanYeonjeContractError(
                f"lifelong Yeonje office ownership changed for {code}"
            )
        result.append(
            _lifelong.BusanOffice(
                code,
                name,
                BUSAN_YEONJE_MUNICIPALITY_CODE,
                BUSAN_YEONJE_MUNICIPALITY_NAME,
                BUSAN_LIFELONG_EXPECTED_OWNERSHIP,
            )
        )
    return tuple(result)


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
                raise BusanYeonjeContractError(
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
    if size > BUSAN_YEONJE_MAX_HTML_BYTES:
        raise _TransientFetchError("source HTML exceeds safety limit")
    return BeautifulSoup(content, "lxml"), final_url


@dataclass
class _FetchResult:
    values: dict[Any, tuple[BeautifulSoup, str]]
    errors: list[str]
    retries: int


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
        for attempt in range(1, BUSAN_YEONJE_FETCH_ATTEMPTS + 1):
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
                if attempt < BUSAN_YEONJE_FETCH_ATTEMPTS:
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
    return _FetchResult(values, errors, retries)


def _probe_local_list(soup: BeautifulSoup) -> None:
    if len(soup.select("form[name='list']")) != 1:
        raise _TransientFetchError("district list root missing")


def _probe_local_detail(soup: BeautifulSoup) -> None:
    if len(soup.select("table.tbl.Thead")) != 1:
        raise _TransientFetchError("district detail root missing")


def _probe_platform_list(soup: BeautifulSoup) -> None:
    if len(soup.select("form#learningVO")) != 1:
        raise _TransientFetchError("lifelong list root missing")


def _probe_platform_detail(soup: BeautifulSoup) -> None:
    if len(soup.select("form#learningVO[name='learningVO']")) != 1:
        raise _TransientFetchError("lifelong detail root missing")


def _probe_city_list(soup: BeautifulSoup) -> None:
    if len(soup.select("form#srchForm[name='srchForm']")) != 1:
        raise _TransientFetchError("Busan city list root missing")


def _probe_city_detail(soup: BeautifulSoup) -> None:
    if len(soup.select("form#viewForm")) != 1:
        raise _TransientFetchError("Busan city detail root missing")


def _local_form_contract(soup: BeautifulSoup, page: int) -> Tag:
    form = _one(soup.select("form[name='list']"), "district list form")
    action = urlparse(urljoin(busan_yeonje_list_url(page), _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or (action.hostname or "").rstrip(".").lower() != BUSAN_YEONJE_HOST
        or action.path != BUSAN_YEONJE_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True) != {"mId": [BUSAN_YEONJE_MENU]}
    ):
        raise BusanYeonjeContractError("district list form changed")
    required = {
        "page": str(page),
        "lecIdx": "0",
        "searchAccept": "",
        "searchTitle": "",
    }
    for name, expected in required.items():
        field = _one(form.select(f"[name='{name}']"), f"district {name} field")
        if _clean(field.get("value")) != expected:
            raise BusanYeonjeContractError(f"district {name} scope changed")
    return form


def _local_last_page(soup: BeautifulSoup) -> int:
    values: set[int] = set()
    for link in soup.select("div.bod_page > a.btn_end[onclick]"):
        match = _LOCAL_PAGE_RE.fullmatch(_clean(link.get("onclick")))
        if match:
            values.add(int(match.group(1)))
    if len(values) != 1:
        raise BusanYeonjeContractError("district last-page control changed")
    return values.pop()


def _local_keyset(node: Tag, identity: str, page: int, action: str) -> None:
    if (
        _clean(node.get("href")) != "#"
        or _clean(node.get("data-action")) != action
        or _clean(node.get("onclick")) != "req.post(this); return false;"
    ):
        raise BusanYeonjeContractError("district identity action changed")
    keyset = _clean(node.get("data-keyset"))
    identities = _LOCAL_KEYSET_ID_RE.findall(keyset)
    pages = _LOCAL_KEYSET_PAGE_RE.findall(keyset)
    if identities != [identity] or pages != [str(page)]:
        raise BusanYeonjeContractError("district action keyset changed")


def _parse_local_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    _local_form_contract(soup, page)
    roots = soup.select("div.lecture_wrap")
    if len(roots) != 1:
        raise BusanYeonjeContractError("expected one district course root")
    cards = roots[0].select(":scope > div.edu_items")
    if sentinel:
        if cards:
            raise BusanYeonjeContractError("district sentinel retained rows")
        return [], expected_last or 0
    last = _local_last_page(soup)
    if expected_last is not None and last != expected_last:
        raise BusanYeonjeContractError("district last page changed")
    current = _one(soup.select("div.bod_page > a.on"), "district current page")
    if _clean(current.get_text(" ", strip=True)) != str(page):
        raise BusanYeonjeContractError("district current-page marker changed")

    rows: list[dict[str, Any]] = []
    for position, card in enumerate(cards, 1):
        heading = _one(card.select(":scope > div.cB > p.lecture_tit"), "district card heading")
        link = _one(heading.select(":scope > a[data-action='view.do']"), "district detail action")
        identity_match = _LOCAL_KEYSET_ID_RE.search(_clean(link.get("data-keyset")))
        if not identity_match:
            raise BusanYeonjeContractError("district identity is missing")
        identity = identity_match.group(1)
        _local_keyset(link, identity, page, "view.do")
        direct = _clean(
            " ".join(
                _clean(child)
                for child in heading.children
                if isinstance(child, NavigableString) and _clean(child)
            )
        )
        if direct != f"No. {identity}":
            raise BusanYeonjeContractError("district display identity changed")
        title = _decoded(link.get_text(" ", strip=True))
        if not title:
            raise BusanYeonjeContractError("district title is empty")

        labels: list[str] = []
        safe: dict[str, str] = {}
        for definition in card.select(":scope > ul.lecture_ul > li > dl"):
            heading_node = _one(definition.find_all("dt", recursive=False), "district card label")
            value_node = _one(definition.find_all("dd", recursive=False), "district card value")
            label = _compact_label(heading_node.get_text(" ", strip=True))
            if label in labels:
                raise BusanYeonjeContractError("duplicate district card label")
            labels.append(label)
            safe[label] = _clean(value_node.get_text(" ", strip=True))
        if tuple(labels) != _LOCAL_LIST_LABELS:
            raise BusanYeonjeContractError("district card labels changed")
        if any(not safe[label] for label in _LOCAL_LIST_LABELS if label != "접수방법"):
            raise BusanYeonjeContractError("district safe card value is empty")
        apply_start, apply_end = _date_range(safe["접수기간"], "district application period")
        start, end = _date_range(safe["학습기간"], "district education period")
        source_status = safe["상태"]
        if source_status not in _LOCAL_STATUS_MAP:
            raise BusanYeonjeContractError(f"unknown district status {source_status!r}")
        active = _LOCAL_STATUS_MAP[source_status] == "OPEN"
        controls = card.select(":scope > div.taC > a[data-action]")
        if active:
            if len(controls) != 2:
                raise BusanYeonjeContractError("open district row lacks exact controls")
            by_action = {_clean(control.get("data-action")): control for control in controls}
            if set(by_action) != {"write.do", "view.do"}:
                raise BusanYeonjeContractError("district control set changed")
            _local_keyset(by_action["write.do"], identity, page, "write.do")
            _local_keyset(by_action["view.do"], identity, page, "view.do")
            if _text(by_action["write.do"]) != "신청하기" or _text(by_action["view.do"]) != "자세히 보기":
                raise BusanYeonjeContractError("district control labels changed")
        elif controls:
            raise BusanYeonjeContractError("closed district row retained controls")

        raw_url = busan_yeonje_detail_url(identity, page)
        branch = safe["교육기관"]
        rows.append(
            {
                "provider": BUSAN_YEONJE_PROVIDER,
                "provider_course_id": f"{BUSAN_YEONJE_PROVIDER}:district:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"yeonje-district-{hashlib.sha1(branch.encode('utf-8')).hexdigest()[:12]}",
                "preserve_branch": True,
                "category": "평생학습",
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
                "target": "",
                "capacity": safe["모집/신청"],
                "venue_name": branch,
                "provider_organizer": branch,
                "municipality_code": BUSAN_YEONJE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_YEONJE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "연제구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_YEONJE_PARSER,
                    "source_catalog": "yeonje_complete_lifelong_catalogue",
                    "source_identity": identity,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "list_application_control": active,
                    "detail_verified": False,
                    "attachments_never_read": True,
                    "free_form_detail_never_read": True,
                    "applicant_values_never_read": True,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    if page < last and len(rows) != BUSAN_YEONJE_PAGE_SIZE:
        raise BusanYeonjeContractError("district intermediate page is short")
    if page == last and not 1 <= len(rows) <= BUSAN_YEONJE_PAGE_SIZE:
        raise BusanYeonjeContractError("district final page count changed")
    return rows, last


def _local_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("raw_fields", {}).get("source_identity")),
            _decoded(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _local_status_key(value: Any) -> tuple[str, str]:
    source = _clean(value)
    if source not in _LOCAL_STATUS_MAP:
        raise BusanYeonjeContractError(f"unknown district status {source!r}")
    reception = (
        "접수중"
        if source.startswith("접수중")
        else "접수마감"
        if source.startswith("접수마감")
        else "교육마감"
    )
    return _LOCAL_STATUS_MAP[source], reception


def _direct_pairs(table: Tag) -> tuple[str, dict[str, str], set[str], bool]:
    title = ""
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    free_form_boundary = False
    rows = table.select(":scope > tbody > tr") or table.find_all("tr", recursive=False)
    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) == 1 and cells[0].name == "th" and _clean(cells[0].get("colspan")) == "4":
            if title:
                raise BusanYeonjeContractError("duplicate district detail title")
            title = _decoded(cells[0].get_text(" ", strip=True))
            continue
        if len(cells) == 1 and cells[0].name == "td" and _clean(cells[0].get("colspan")) == "4":
            if len(cells[0].select(":scope .pad10a")) != 1 or free_form_boundary:
                raise BusanYeonjeContractError("district free-form boundary changed")
            free_form_boundary = True
            continue
        if len(cells) % 2:
            raise BusanYeonjeContractError("district detail cells changed")
        for index in range(0, len(cells), 2):
            heading, value = cells[index : index + 2]
            if heading.name != "th" or value.name != "td":
                raise BusanYeonjeContractError("district detail label/value order changed")
            label = _compact_label(heading.get_text(" ", strip=True))
            if not label or label in labels:
                raise BusanYeonjeContractError("district detail label changed")
            labels.append(label)
            if label in _LOCAL_DETAIL_SAFE_LABELS:
                safe[label] = _clean(value.get_text(" ", strip=True))
            else:
                skipped.add(label)
    if tuple(labels) != _LOCAL_DETAIL_LABELS:
        raise BusanYeonjeContractError("district detail labels changed")
    if not title or not free_form_boundary:
        raise BusanYeonjeContractError("district safe detail structure changed")
    if not _LOCAL_DETAIL_PRIVATE_LABELS.issubset(skipped):
        raise BusanYeonjeContractError("district private field boundary changed")
    if any(
        not safe.get(label)
        for label in _LOCAL_DETAIL_SAFE_LABELS - _LOCAL_DETAIL_ALLOW_BLANK
    ):
        raise BusanYeonjeContractError("district safe detail value is empty")
    return title, safe, skipped, free_form_boundary


def _parse_local_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    page = int(raw.get("source_page") or 0)
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONJE_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_YEONJE_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"mId", "lecIdx", "page"}
        or query.get("mId") != [BUSAN_YEONJE_MENU]
        or query.get("lecIdx") != [identity]
        or query.get("page") != [str(page)]
    ):
        raise BusanYeonjeContractError("district detail response scope changed")
    form = _one(soup.select("form[name='list']"), "district detail return form")
    action = urlparse(urljoin(final_url, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != BUSAN_YEONJE_LIST_PATH
        or parse_qs(action.query, keep_blank_values=True) != {"mId": [BUSAN_YEONJE_MENU]}
    ):
        raise BusanYeonjeContractError("district detail return form changed")
    for name, expected in (("lecIdx", identity), ("page", str(page))):
        field = _one(form.select(f"[name='{name}']"), f"district detail {name}")
        if _clean(field.get("value")) != expected:
            raise BusanYeonjeContractError("district detail identity changed")

    table = _one(soup.select("table.tbl.Thead"), "district safe detail table")
    title, safe, skipped, _free_form = _direct_pairs(table)
    if title != _decoded(parent.get("title")):
        raise BusanYeonjeContractError("district list/detail title mismatch")
    start, end = _date_range(safe["학습기간"], "district detail education period")
    apply_start, apply_end = _date_range(safe["접수기간"], "district detail application period")
    if (start, end) != (_clean(parent.get("start_date")), _clean(parent.get("end_date"))):
        raise BusanYeonjeContractError("district list/detail education dates mismatch")
    if (apply_start, apply_end) != (_clean(parent.get("apply_start")), _clean(parent.get("apply_end"))):
        raise BusanYeonjeContractError("district list/detail application dates mismatch")
    for label, expected in (
        ("학습기관", parent.get("branch")),
        ("접수방법", parent.get("application_method_raw")),
    ):
        if _clean(safe[label]) != _clean(expected):
            raise BusanYeonjeContractError(f"district list/detail {label} mismatch")
    if _local_status_key(safe["상태"]) != _local_status_key(raw.get("source_status")):
        raise BusanYeonjeContractError("district list/detail 상태 mismatch")

    applicant = _one(soup.select("table.tbl.taC"), "district applicant boundary")
    applicant_headers = tuple(
        _compact_label(node.get_text(" ", strip=True)) for node in applicant.select("thead th")
    )
    if applicant_headers != _LOCAL_APPLICANT_HEADERS:
        raise BusanYeonjeContractError("district applicant boundary changed")
    controls = soup.select("a[data-action]")
    by_action: dict[str, list[Tag]] = {}
    for control in controls:
        by_action.setdefault(_clean(control.get("data-action")), []).append(control)
    if set(by_action) not in ({"list.do"}, {"list.do", "write.do"}):
        raise BusanYeonjeContractError("district detail control set changed")
    list_control = _one(by_action.get("list.do", []), "district list control")
    if _text(list_control) != "목록":
        raise BusanYeonjeContractError("district list control changed")
    active = _LOCAL_STATUS_MAP[_clean(raw.get("source_status"))] == "OPEN"
    write_controls = by_action.get("write.do", [])
    if active:
        write = _one(write_controls, "district application control")
        _local_keyset(write, identity, page, "write.do")
        if _text(write) != "신청하기" or "온라인" not in _clean(safe["접수방법"]):
            raise BusanYeonjeContractError("district application control changed")
    elif write_controls:
        raise BusanYeonjeContractError("closed district detail retained application")

    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
            "category": safe["사업명"],
            "fee": safe["수강료"],
            "material_fee": safe["추가비용"],
            "schedule_raw": safe["교육시간"],
            "target": safe["교육대상"],
            "capacity": safe["교육정원"],
            "venue_name": safe["교육장소"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": safe["상태"],
        "detail_application_control": active,
        "instructor_value_never_read": "강사명" in skipped,
        "contact_value_never_read": "교육문의전화" in skipped,
        "attachments_never_read": {"신청서", "기타파일"}.issubset(skipped),
        "free_form_detail_never_read": True,
        "applicant_values_never_read": True,
        "application_form_fetched": False,
    }
    return result


def _node_text_without(node: Any, selectors: tuple[str, ...]) -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "lxml")
    for selector in selectors:
        for part in clone.select(selector):
            part.extract()
    return _clean(clone.get_text(" ", strip=True))


def _platform_last_page(soup: BeautifulSoup) -> int:
    values: set[int] = set()
    for link in soup.select("a.page_nextend"):
        text = " ".join(
            part
            for part in (_clean(link.get("href")), _clean(link.get("onclick")))
            if part
        )
        match = _PLATFORM_PAGE_RE.search(text)
        if match:
            values.add(int(match.group(1)))
    if len(values) != 1 or next(iter(values)) < 1:
        raise BusanYeonjeContractError("lifelong last-page control changed")
    return values.pop()


def _platform_form_contract(
    soup: BeautifulSoup, office: _lifelong.BusanOffice, page: int
) -> None:
    form = _one(soup.select("form#learningVO"), "lifelong list form")
    action = urlparse(urljoin(BUSAN_LIFELONG_LIST_URL, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or (action.hostname or "").rstrip(".").lower()
        != _lifelong.BUSAN_LIFELONG_HOST
        or action.path != BUSAN_LIFELONG_LIST_PATH
    ):
        raise BusanYeonjeContractError("lifelong list form changed")
    for name, expected in (
        ("inst_id", office.code),
        ("display_type", "2"),
        ("pageIndex", str(page)),
        ("pageUnit", str(BUSAN_LIFELONG_PAGE_SIZE)),
        ("l_search_ch", "0"),
    ):
        field = _one(form.select(f"[name='{name}']"), f"lifelong {name}")
        actual = _clean(field.get("value"))
        if field.name == "select":
            selected = field.select(":scope > option[selected]")
            if name == "pageUnit":
                # The audited server honors the explicit request-side value
                # 1000 but renders its ordinary UI choices (10/20/50) with no
                # selected option.  The complete sequence and displayed last
                # page below prove that the requested census was honored.
                options = [
                    _clean(option.get("value"))
                    for option in field.select(":scope > option[value]")
                ]
                if options != ["10", "20", "50"] or selected:
                    raise BusanYeonjeContractError(
                        "lifelong pageUnit selector changed"
                    )
                actual = expected
            else:
                actual = _clean(
                    _one(selected, f"lifelong selected {name}").get("value")
                )
        if actual != expected:
            raise BusanYeonjeContractError(f"lifelong {name} scope changed")
    selected_office = _one(
        form.select("#o_search_ch option[selected]"), "lifelong selected office"
    )
    selected_state = _one(
        form.select("#learning_state option[selected]"), "lifelong selected state"
    )
    if (
        _clean(selected_office.get("value")) != office.code
        or _clean(selected_state.get("value")) != "0"
    ):
        raise BusanYeonjeContractError("lifelong office/status partition changed")


def _safe_yeonje_external_url(value: Any) -> tuple[str, str]:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_YEONJE_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_YEONJE_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"mId", "lecIdx"}
        or query.get("mId") != [BUSAN_YEONJE_EXTERNAL_MENU]
    ):
        return "", ""
    identity = _query_one(query, "lecIdx")
    if not _IDENTITY_RE.fullmatch(identity):
        return "", ""
    url = f"https://{BUSAN_YEONJE_HOST}{BUSAN_YEONJE_DETAIL_PATH}?" + urlencode(
        (("mId", BUSAN_YEONJE_EXTERNAL_MENU), ("lecIdx", identity))
    )
    return url, identity


def _platform_semantic_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _decoded(row.get("title")),
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
        _clean(row.get("apply_start")),
        _clean(row.get("apply_end")),
    )


def _parse_platform_page(
    soup: BeautifulSoup,
    *,
    office: _lifelong.BusanOffice,
    page: int,
    expected_last: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    _platform_form_contract(soup, office, page)
    last = _platform_last_page(soup)
    if expected_last is not None and last != expected_last:
        raise BusanYeonjeContractError("lifelong displayed last page changed")
    tables = [table for table in soup.select("table") if len(table.select("thead th")) == 7]
    table = _one(tables, "lifelong course table")
    headings = [_clean(node.get_text(" ", strip=True)) for node in table.select("thead th")]
    required = ("번호", "강좌명", "재료비", "교육기간", "신청기간", "상태", "보기")
    if len(headings) != 7 or any(token not in headings[index] for index, token in enumerate(required)):
        raise BusanYeonjeContractError("lifelong table headers changed")

    rows: list[dict[str, Any]] = []
    source_rows = table.select("tbody tr") or [row for row in table.select("tr") if row.select("td")]
    for source_row in source_rows:
        cells = source_row.select("td")
        title_link = source_row.select_one("td.subject a")
        if title_link is None:
            # Empty pages contain only a no-record placeholder.  Its value is
            # not retained and no detail/private node is traversed.
            if len(cells) == 1:
                continue
            raise BusanYeonjeContractError("lifelong non-course row changed")
        if len(cells) != 7:
            raise BusanYeonjeContractError("lifelong course row width changed")
        sequence_raw = _clean(cells[0].get_text(" ", strip=True)).replace(",", "")
        if not sequence_raw.isdigit() or int(sequence_raw) < 1:
            raise BusanYeonjeContractError("lifelong sequence changed")
        title_node = _one(title_link.select(":scope .tit"), "lifelong title")
        office_node = _one(title_link.select(":scope .org"), "lifelong office")
        title = _decoded(title_node.get_text(" ", strip=True))
        if not title or _clean(office_node.get_text(" ", strip=True)) != office.name:
            raise BusanYeonjeContractError("lifelong title/office changed")

        onclick = _clean(title_link.get("onclick"))
        internal = _PLATFORM_INTERNAL_RE.search(onclick)
        identity = ""
        identity_kind = ""
        raw_url = ""
        external_local_identity = ""
        if internal:
            identity = internal.group(1)
            identity_kind = "internal"
            raw_url = busan_yeonje_lifelong_detail_url(identity)
        else:
            href = _clean(title_link.get("href"))
            if href:
                raw_url, external_local_identity = _safe_yeonje_external_url(href)
                if not raw_url:
                    raise BusanYeonjeContractError("unsafe lifelong external URL")
                identity = raw_url
                identity_kind = "external"
            elif not onclick:
                identity_kind = "list_only_semantic_v1"
            else:
                raise BusanYeonjeContractError("lifelong identity action changed")

        period_node = cells[3].select_one(".s_type.blue")
        period_text = _node_text_without(period_node, ("em.hidden", "pre"))
        period_dates = _platform_dates(period_text)
        if len(period_dates) != 2:
            raise BusanYeonjeContractError("lifelong education period changed")
        reversed_period = period_dates[1] < period_dates[0]
        start, end = sorted(period_dates)
        schedule_node = cells[3].select_one("pre")
        schedule = _clean(schedule_node.get_text(" ", strip=True) if schedule_node else "")

        apply_node = cells[4].select_one(".s_type.red1")
        apply_dates = _platform_dates(_node_text_without(apply_node, ("em.hidden",)))
        if len(apply_dates) not in (0, 2):
            raise BusanYeonjeContractError("lifelong application period changed")
        reversed_apply_period = bool(
            len(apply_dates) == 2 and apply_dates[1] < apply_dates[0]
        )
        normalized_apply_dates = sorted(apply_dates)
        apply_start = normalized_apply_dates[0].isoformat() if apply_dates else ""
        apply_end = normalized_apply_dates[1].isoformat() if apply_dates else ""

        # Only the first span is the material-fee field.  Later spans may hold
        # instructor/contact values and are deliberately never converted to text.
        fee_node = cells[2].select_one(":scope > span")
        fee = _clean(fee_node.get_text(" ", strip=True) if fee_node else "")
        if not fee:
            raise BusanYeonjeContractError("lifelong fee changed")
        capacity_node = cells[4].select_one(".s_type.indigo1")
        capacity = _node_text_without(capacity_node, ("em.hidden",))
        if not capacity:
            raise BusanYeonjeContractError("lifelong capacity changed")
        status_values = [
            _clean(node.get_text(" ", strip=True)) for node in cells[5].select(".s_btn")
        ]
        status_values = [value for value in status_values if value]
        source_status = status_values[0] if status_values else ""
        if source_status not in _PLATFORM_STATUS_MAP:
            raise BusanYeonjeContractError(f"unknown lifelong status {source_status!r}")
        selection_node = cells[5].select_one(".s_type2 em.hidden")
        selection_method = _clean(
            selection_node.get_text(" ", strip=True) if selection_node else ""
        )

        if identity_kind == "list_only_semantic_v1":
            semantic = (
                office.code,
                title,
                start.isoformat(),
                end.isoformat(),
                apply_start,
                apply_end,
                schedule,
            )
            digest = hashlib.sha256("\x1f".join(semantic).encode("utf-8")).hexdigest()[:32]
            identity = f"LIST_ONLY_V1:{digest}"
            raw_url = busan_yeonje_lifelong_list_url(office.code, page)

        rows.append(
            {
                "provider": BUSAN_LIFELONG_PROVIDER,
                "provider_course_id": f"{BUSAN_LIFELONG_PROVIDER}:course:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": BUSAN_YEONJE_MUNICIPALITY_NAME,
                "branch_code": BUSAN_YEONJE_MUNICIPALITY_CODE,
                "preserve_branch": True,
                "category": "평생학습",
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": selection_method,
                "reservation_available": False,
                "status": _PLATFORM_STATUS_MAP[source_status],
                "fee": fee,
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": f"{apply_start} ~ {apply_end}" if apply_start else "",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": schedule,
                "target": "",
                "capacity": capacity,
                "venue_name": office.name,
                "provider_organizer": office.name,
                "municipality_code": BUSAN_YEONJE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_YEONJE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "연제구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_shared_office+native_current_detail",
                "raw_fields": {
                    "parser": BUSAN_YEONJE_PARSER,
                    "source_catalog": "busan_lifelong_yeonje",
                    "source_provider": BUSAN_LIFELONG_PROVIDER,
                    "identity": identity,
                    "identity_kind": identity_kind,
                    "external_local_identity": external_local_identity,
                    "source_office_code": office.code,
                    "source_office_name": office.name,
                    "list_page": page,
                    "list_sequence": int(sequence_raw),
                    "source_status": source_status,
                    "selection_method": selection_method,
                    "source_reversed_education_period": reversed_period,
                    "source_reversed_application_period": reversed_apply_period,
                    "detail_verified": False,
                    "instructor_value_never_read": True,
                    "contact_value_never_read": True,
                    "attachments_never_read": True,
                    "free_form_detail_never_read": True,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    if sentinel and rows:
        raise BusanYeonjeContractError("lifelong sentinel retained rows")
    if not sentinel:
        if page < last and len(rows) != BUSAN_LIFELONG_PAGE_SIZE:
            raise BusanYeonjeContractError("lifelong intermediate page is short")
        if page == last and len(rows) > BUSAN_LIFELONG_PAGE_SIZE:
            raise BusanYeonjeContractError("lifelong final page is oversized")
    return rows, last


def _platform_archive_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("raw_fields", {}).get("identity_kind")),
            *_platform_semantic_key(row),
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
        raise BusanYeonjeContractError("invalid native lifelong identity")
    result.update(
        {
            "provider": BUSAN_YEONJE_PROVIDER,
            "provider_course_id": f"{BUSAN_YEONJE_PROVIDER}:lifelong:{identity}",
            "municipality_code": BUSAN_YEONJE_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_YEONJE_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "연제구",
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
        "parser": BUSAN_YEONJE_PARSER,
        "source_catalog": "busan_lifelong_yeonje_native",
        "detail_verified": False,
    }
    return result


def _safe_platform_detail_values(
    soup: BeautifulSoup,
) -> tuple[tuple[str, ...], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    allowed = set(_PLATFORM_DETAIL_REQUIRED_LABELS) | set(_PLATFORM_DETAIL_OPTIONAL_LABELS)
    for definition in soup.select("div.form_group dl"):
        heading = _one(definition.find_all("dt", recursive=False), "lifelong detail label")
        value = _one(definition.find_all("dd", recursive=False), "lifelong detail value")
        label = _text(heading)
        if not label or label in labels or label not in allowed:
            raise BusanYeonjeContractError(f"unknown or duplicate lifelong detail field {label!r}")
        labels.append(label)
        if label in _PLATFORM_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    without_optional = [label for label in labels if label not in _PLATFORM_DETAIL_OPTIONAL_LABELS]
    if tuple(without_optional) != _PLATFORM_DETAIL_REQUIRED_LABELS:
        raise BusanYeonjeContractError("lifelong detail field order changed")
    expected_skipped = (
        set(_PLATFORM_DETAIL_REQUIRED_LABELS)
        | (set(labels) & set(_PLATFORM_DETAIL_OPTIONAL_LABELS))
    ) - set(_PLATFORM_DETAIL_SAFE_LABELS)
    if skipped != expected_skipped:
        raise BusanYeonjeContractError("lifelong private field boundary changed")
    return tuple(labels), safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    office_code = _clean(raw.get("source_office_code"))
    office_name = _clean(raw.get("source_office_name"))
    parsed = urlparse(final_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != _lifelong.BUSAN_LIFELONG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_LIFELONG_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"lng_id"}
        or query.get("lng_id") != [identity]
    ):
        raise BusanYeonjeContractError("lifelong detail response scope changed")
    form = _one(soup.select("form#learningVO[name='learningVO']"), "lifelong detail form")
    action = urlparse(urljoin(final_url, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != BUSAN_LIFELONG_DETAIL_PATH
        or parse_qs(action.query, keep_blank_values=True).get("lng_id") != [identity]
    ):
        raise BusanYeonjeContractError("lifelong detail form changed")
    identity_fields = {_clean(node.get("value")) for node in form.select("input[name='lng_id']")}
    office_fields = {_clean(node.get("value")) for node in form.select("input[name='inst_id']")}
    if identity_fields != {identity} or office_fields != {office_code}:
        raise BusanYeonjeContractError("lifelong detail identity/office mismatch")
    heading = _one(soup.select("h2.enrolTit"), "lifelong detail heading")
    prefix = _one(heading.select(":scope > span"), "lifelong detail office prefix")
    if _text(prefix) != f"[{office_name}]":
        raise BusanYeonjeContractError("lifelong detail office prefix changed")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _decoded(clone.get_text(" ", strip=True)) != _decoded(parent.get("title")):
        raise BusanYeonjeContractError("lifelong list/detail title mismatch")
    labels, safe, skipped = _safe_platform_detail_values(soup)
    start, end = _date_range(safe["교육기간"], "lifelong detail education period")
    if (start, end) != (_clean(parent.get("start_date")), _clean(parent.get("end_date"))):
        raise BusanYeonjeContractError("lifelong list/detail education dates mismatch")
    if parent.get("apply_start") and parent.get("apply_end"):
        apply_start, apply_end = _date_range(
            safe["일반모집기간"], "lifelong detail application period"
        )
        if (apply_start, apply_end) != (
            _clean(parent.get("apply_start")),
            _clean(parent.get("apply_end")),
        ):
            raise BusanYeonjeContractError("lifelong list/detail application dates mismatch")

    controls = soup.select("#learning_aply_btn")
    source_status = _clean(raw.get("source_status"))
    active = bool(controls)
    control_label = ""
    application_type = "INFO_ONLY"
    if active:
        control = _one(controls, "lifelong application control")
        control_label = _text(control)
        if (
            source_status not in {"접수중", "대기접수"}
            or control_label not in {"우선모집신청", "일반모집신청", "수강신청", "대기자신청"}
            or _clean(control.get("onclick")) != "fn_learning_apply(); return false;"
        ):
            raise BusanYeonjeContractError("lifelong application control changed")
        application_type = "WAITLIST_APPLY" if control_label == "대기자신청" else "ONLINE_RESERVATION"
    elif source_status in {"접수중", "대기접수"}:
        raise BusanYeonjeContractError("open lifelong row lost application control")

    result = dict(parent)
    result.update(
        {
            "status": "OPEN" if active else _PLATFORM_STATUS_MAP[source_status],
            "application_url": busan_yeonje_lifelong_detail_url(identity) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "target": safe.get("교육대상", ""),
            "venue_name": safe.get("교육장소") or office_name,
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
        "contact_value_never_read": "문의전화" in skipped,
        "instructor_value_never_read": "강사" in skipped,
        "enrollment_counts_never_read": "접수인원" in skipped,
        "optional_free_form_values_never_read": True,
        "workplace_eligibility_value_never_read": "직장인 여부" in labels,
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_last: Optional[int] = None,
    sentinel: bool = False,
) -> tuple[int, Optional[Tag]]:
    if _text(_one(soup.select("title"), "Busan city title")) != _CITY_LIST_TITLE:
        raise BusanYeonjeContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "Busan city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
        or _clean(_one(form.select("input[name='curPage']"), "Busan city page field").get("value"))
        != str(page)
    ):
        raise BusanYeonjeContractError("Busan city search form changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_YEONJE_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanYeonjeContractError(f"Busan city {name} filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "Busan city last page")
    parsed = urlparse(urljoin(BUSAN_CITY_YEONJE_URL, _clean(end_link.get("href"))))
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
        or query.get("srchGugun") != [BUSAN_CITY_YEONJE_GUGUN]
        or query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
    ):
        raise BusanYeonjeContractError("unsafe Busan city last-page control")
    last_raw = _query_one(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanYeonjeContractError("invalid Busan city last page")
    last = int(last_raw)
    if expected_last is not None and last != expected_last:
        raise BusanYeonjeContractError("Busan city last page changed")
    roots = soup.select("ul.reserveList")
    if sentinel:
        if roots:
            raise BusanYeonjeContractError("Busan city sentinel retained list")
        return last, None
    return last, _one(roots, "Busan city reserve list")


def _city_card_date_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanYeonjeContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (
            date.fromisoformat(part) for part in match.groups()
        )
    except ValueError as exc:
        raise BusanYeonjeContractError(f"{label} contains invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanYeonjeContractError(f"{label} is reversed")
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
    sentinel: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(
        soup, page=page, expected_last=expected_last, sentinel=sentinel
    )
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "Busan city course")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanYeonjeContractError("Busan city identity action changed")
        group_id, program_id = action.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        normalized_title_attribute = title_attribute
        if title.startswith("[권역]") and title_attribute == title.removeprefix("[권역]"):
            normalized_title_attribute = title
        if not title or normalized_title_attribute != title:
            raise BusanYeonjeContractError("Busan city card title changed")
        source_status = _text(_one(link.select(":scope .statusMark"), "Busan city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanYeonjeContractError("unknown Busan city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "Busan city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanYeonjeContractError("Busan city card labels changed")
        # 문의 is the final pair and its value is intentionally never read.
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanYeonjeContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if not re.fullmatch(r"연제구 .+ 주민자치회", branch):
            raise BusanYeonjeContractError("Busan city row left Yeonje owner")
        apply_start, apply_end, start, end = _city_card_date_ranges(
            safe["일자"], f"Busan city page {page} row {position} dates"
        )
        raw_url = busan_yeonje_city_detail_url(group_id, program_id)
        rows.append(
            {
                "provider": BUSAN_YEONJE_PROVIDER,
                "provider_course_id": f"{BUSAN_YEONJE_PROVIDER}:reserve:{group_id}:{program_id}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"yeonje-reserve-{group_id}",
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
                "municipality_code": BUSAN_YEONJE_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_YEONJE_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "연제구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_YEONJE_PARSER,
                    "source_catalog": "busan_reserve_yeonje_resident_councils",
                    "source_identity": f"{group_id}:{program_id}",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": safe["방법"],
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "service_family": "education",
                },
            }
        )
    if sentinel and rows:
        raise BusanYeonjeContractError("Busan city sentinel retained rows")
    if not sentinel:
        if page < last and len(rows) != 10:
            raise BusanYeonjeContractError("Busan city intermediate page is short")
        if page == last and not 1 <= len(rows) <= 10:
            raise BusanYeonjeContractError("Busan city final page count changed")
    return rows, last


def _city_signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _city_detail_dates(value: Any, label: str) -> tuple[str, str]:
    found = _CITY_DETAIL_DATE_RE.findall(_clean(value))
    if len(found) != 2:
        raise BusanYeonjeContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(part) for part in found)
    except ValueError as exc:
        raise BusanYeonjeContractError(f"{label} has invalid date") from exc
    if end < start:
        raise BusanYeonjeContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _city_method_key(value: Any) -> str:
    return "".join(re.sub(r",\s*,", ",", _clean(value)).split())


def _safe_city_detail_values(info: Tag) -> tuple[tuple[str, ...], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(definition.find_all("dt", recursive=False), "Busan city detail label")
        value = _one(definition.find_all("dd", recursive=False), "Busan city detail value")
        label = _text(heading)
        if label in labels:
            raise BusanYeonjeContractError("duplicate Busan city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _CITY_DETAIL_SKIPPED_LABELS:
            skipped.add(label)
        else:
            raise BusanYeonjeContractError(f"unknown Busan city detail field {label!r}")
    without_attachment = [label for label in labels if label != "첨부파일"]
    if tuple(without_attachment) != _CITY_DETAIL_REQUIRED_LABELS or "문의전화" not in skipped:
        raise BusanYeonjeContractError("Busan city detail/private boundary changed")
    return tuple(labels), safe, skipped


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
        raise BusanYeonjeContractError("Busan city detail response scope changed")
    if _text(_one(soup.select("title"), "Busan city detail title")) != _CITY_LIST_TITLE:
        raise BusanYeonjeContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanYeonjeContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"Busan city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanYeonjeContractError("Busan city detail identity changed")
    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "Busan city detail heading")
    source_status = _text(_one(heading.select(":scope .statusMark"), "Busan city detail status"))
    direct_title = _clean(
        " ".join(
            _clean(child)
            for child in heading.children
            if isinstance(child, NavigableString) and _clean(child)
        )
    )
    if direct_title != _clean(parent.get("title")) or source_status != _clean(raw.get("source_status")):
        raise BusanYeonjeContractError("Busan city list/detail title or status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    _labels, safe, skipped = _safe_city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanYeonjeContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanYeonjeContractError("Busan city free-form boundary changed")
    start, end = _city_detail_dates(safe["운영기간"], "Busan city operating period")
    apply_start, apply_end = _city_detail_dates(safe["신청기간"], "Busan city application period")
    if (start, end) != (_clean(parent.get("start_date")), _clean(parent.get("end_date"))):
        raise BusanYeonjeContractError("Busan city list/detail operating dates mismatch")
    if (apply_start, apply_end) != (_clean(parent.get("apply_start")), _clean(parent.get("apply_end"))):
        raise BusanYeonjeContractError("Busan city list/detail application dates mismatch")
    for label, expected in (
        ("신청방법", raw.get("source_application_method")),
        ("운영기관", parent.get("branch")),
        ("대상", parent.get("target")),
    ):
        actual_key = _city_method_key(safe[label]) if label == "신청방법" else _clean(safe[label])
        expected_key = _city_method_key(expected) if label == "신청방법" else _clean(expected)
        if actual_key != expected_key:
            raise BusanYeonjeContractError(f"Busan city list/detail {label} mismatch")

    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanYeonjeContractError("multiple Busan city application controls")
    control_label = _text(controls[0]) if controls else ""
    normalized_status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if not controls or not any(token in control_label for token in ("신청", "예약")):
                raise BusanYeonjeContractError("open online Busan city row lacks control")
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("방문", "전화")):
            if control_label not in {"", "방문예약", "전화접수"}:
                raise BusanYeonjeContractError("offline Busan city control changed")
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanYeonjeContractError("unknown Busan city application method")
    elif normalized_status == "CLOSED":
        if control_label not in {"", "접수마감"}:
            raise BusanYeonjeContractError("closed Busan city control changed")
    elif normalized_status == "SCHEDULED" and control_label not in {"", "대기중", "접수대기"}:
        raise BusanYeonjeContractError("scheduled Busan city control changed")

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
        "attachments_never_read": "첨부파일" in skipped or "첨부파일" not in _labels,
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


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "required_list_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "required_detail_requests": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "source_cap_reached": False,
        "district_source_rows": 0,
        "district_data_pages": 0,
        "district_current_count": 0,
        "platform_source_rows": 0,
        "platform_rows_by_office": {},
        "platform_external_rows": 0,
        "platform_list_only_rows": 0,
        "platform_external_duplicate_rows": 0,
        "platform_list_only_duplicate_rows": 0,
        "platform_native_rows": 0,
        "platform_native_current_count": 0,
        "platform_semantic_censuses": 0,
        "city_source_rows": 0,
        "city_data_pages": 0,
        "city_current_count": 0,
        "source_total": 0,
        "unique_education_source_rows": 0,
        "duplicate_source_identity_count": 0,
        "current_source_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "application_control_count": 0,
        "offline_application_count": 0,
        "status_counts": {},
        "pii_fields_never_read": list(BUSAN_YEONJE_PII_FIELDS_NEVER_READ),
        "snapshot_complete": False,
        "atomic_union_complete": False,
        "configured_collection_error": "",
    }


def _fail(
    meta: dict[str, Any], budget: Optional[_RequestBudget], message: Any
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    if budget is not None:
        meta["network_requests"] = budget.count
    meta["configured_collection_error"] = _clean(message)
    meta["snapshot_complete"] = False
    meta["atomic_union_complete"] = False
    return [], BUSAN_YEONJE_PARSER, meta


def collect_busan_yeonje_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 300,
    max_requests: int = 600,
    *,
    session_factory: Optional[SessionFactory] = None,
    fetcher: Optional[Fetcher] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BUSAN_YEONJE_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
    sleeper: Sleeper = time.sleep,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete current/future Yeonje-gu education snapshot."""

    meta = _base_meta()
    if not is_busan_yeonje_education_target(target):
        return _fail(meta, None, "target does not match the canonical Yeonje education owner")
    budget: Optional[_RequestBudget] = None
    try:
        request_timeout = _positive_int(timeout, "timeout")
        allowed_pages = _positive_int(max_pages, "max_pages")
        allowed_details = _positive_int(detail_limit, "detail_limit")
        allowed_requests = _positive_int(max_requests, "max_requests")
        workers = min(_positive_int(max_workers, "max_workers"), BUSAN_YEONJE_MAX_WORKERS)
        cutoff = _today(today)
        offices = _platform_offices()
        fetch = fetcher or _default_fetcher
        sessions = session_factory or _default_session_factory
        dedupe = dedupe_rows or _default_dedupe
        budget = _RequestBudget(allowed_requests)

        initial_items: list[tuple[Any, str, Probe]] = [
            (("local", "data", 1), busan_yeonje_list_url(1), _probe_local_list),
            (("city", "data", 1), busan_yeonje_city_list_url(1), _probe_city_list),
        ]
        for office in offices:
            initial_items.append(
                (
                    ("platform", office.code, "a", 1),
                    busan_yeonje_lifelong_list_url(office.code, 1),
                    _probe_platform_list,
                )
            )
        initial = _fetch_many(
            initial_items,
            fetcher=fetch,
            session_factory=sessions,
            timeout=request_timeout,
            max_workers=workers,
            sleeper=sleeper,
            budget=budget,
        )
        meta["network_retry_count"] += initial.retries
        if initial.errors:
            raise BusanYeonjeContractError("; ".join(initial.errors))

        local_first, local_last = _parse_local_page(
            initial.values[("local", "data", 1)][0], page=1
        )
        city_first, city_last = _parse_city_page(
            initial.values[("city", "data", 1)][0], page=1
        )
        platform_first: dict[str, list[dict[str, Any]]] = {}
        platform_lasts: dict[str, int] = {}
        for office in offices:
            rows, last = _parse_platform_page(
                initial.values[("platform", office.code, "a", 1)][0],
                office=office,
                page=1,
            )
            platform_first[office.code] = rows
            platform_lasts[office.code] = last

        local_boundaries = sorted({1, local_last})
        city_boundaries = sorted({1, city_last})
        required_list_requests = (
            local_last
            + 1
            + len(local_boundaries)
            + city_last
            + 1
            + len(city_boundaries)
            + sum(2 * platform_lasts[office.code] + 1 for office in offices)
        )
        meta["required_list_requests"] = required_list_requests
        meta["sentinel_requests"] = 2 + len(offices)
        meta["stability_rechecks"] = (
            len(local_boundaries)
            + len(city_boundaries)
            + sum(platform_lasts[office.code] for office in offices)
        )
        if required_list_requests > allowed_pages:
            meta["source_cap_reached"] = True
            raise BusanYeonjeContractError(
                f"max_pages cap allows {allowed_pages} of {required_list_requests} required list requests"
            )
        if required_list_requests > allowed_requests:
            meta["source_cap_reached"] = True
            raise BusanYeonjeContractError(
                f"max_requests cap allows {allowed_requests} of at least {required_list_requests} requests"
            )

        remaining_items: list[tuple[Any, str, Probe]] = []
        for page in range(2, local_last + 1):
            remaining_items.append(
                (("local", "data", page), busan_yeonje_list_url(page), _probe_local_list)
            )
        remaining_items.append(
            (("local", "sentinel", local_last + 1), busan_yeonje_list_url(local_last + 1), _probe_local_list)
        )
        for page in local_boundaries:
            remaining_items.append(
                (("local", "recheck", page), busan_yeonje_list_url(page), _probe_local_list)
            )
        for office in offices:
            last = platform_lasts[office.code]
            for page in range(2, last + 1):
                remaining_items.append(
                    (
                        ("platform", office.code, "a", page),
                        busan_yeonje_lifelong_list_url(office.code, page),
                        _probe_platform_list,
                    )
                )
            for page in range(1, last + 1):
                remaining_items.append(
                    (
                        ("platform", office.code, "b", page),
                        busan_yeonje_lifelong_list_url(office.code, page),
                        _probe_platform_list,
                    )
                )
            remaining_items.append(
                (
                    ("platform", office.code, "sentinel", last + 1),
                    busan_yeonje_lifelong_list_url(office.code, last + 1),
                    _probe_platform_list,
                )
            )
        for page in range(2, city_last + 1):
            remaining_items.append(
                (("city", "data", page), busan_yeonje_city_list_url(page), _probe_city_list)
            )
        remaining_items.append(
            (("city", "sentinel", city_last + 1), busan_yeonje_city_list_url(city_last + 1), _probe_city_list)
        )
        for page in city_boundaries:
            remaining_items.append(
                (("city", "recheck", page), busan_yeonje_city_list_url(page), _probe_city_list)
            )
        remaining = _fetch_many(
            remaining_items,
            fetcher=fetch,
            session_factory=sessions,
            timeout=request_timeout,
            max_workers=workers,
            sleeper=sleeper,
            budget=budget,
        )
        meta["network_retry_count"] += remaining.retries
        if remaining.errors:
            raise BusanYeonjeContractError("; ".join(remaining.errors))
        values = {**initial.values, **remaining.values}

        local_rows: list[dict[str, Any]] = []
        local_page_rows: dict[int, list[dict[str, Any]]] = {}
        for page in range(1, local_last + 1):
            if page == 1:
                rows = local_first
            else:
                rows, _ = _parse_local_page(
                    values[("local", "data", page)][0],
                    page=page,
                    expected_last=local_last,
                )
            local_page_rows[page] = rows
            local_rows.extend(rows)
        _parse_local_page(
            values[("local", "sentinel", local_last + 1)][0],
            page=local_last + 1,
            expected_last=local_last,
            sentinel=True,
        )
        for page in local_boundaries:
            rechecked, _ = _parse_local_page(
                values[("local", "recheck", page)][0],
                page=page,
                expected_last=local_last,
            )
            if _local_signature(rechecked) != _local_signature(local_page_rows[page]):
                raise BusanYeonjeContractError(f"district boundary page {page} changed")
        local_ids = [_clean(row.get("raw_fields", {}).get("source_identity")) for row in local_rows]
        if len(local_ids) != len(set(local_ids)):
            raise BusanYeonjeContractError("duplicate district identities")

        platform_rows: list[dict[str, Any]] = []
        platform_rows_by_office: dict[str, int] = {}
        for office in offices:
            last = platform_lasts[office.code]
            census_a: list[dict[str, Any]] = []
            census_b: list[dict[str, Any]] = []
            for census, destination in (("a", census_a), ("b", census_b)):
                for page in range(1, last + 1):
                    if census == "a" and page == 1:
                        rows = platform_first[office.code]
                    else:
                        rows, _ = _parse_platform_page(
                            values[("platform", office.code, census, page)][0],
                            office=office,
                            page=page,
                            expected_last=last,
                        )
                    destination.extend(rows)
            _parse_platform_page(
                values[("platform", office.code, "sentinel", last + 1)][0],
                office=office,
                page=last + 1,
                expected_last=last,
                sentinel=True,
            )
            for census in (census_a, census_b):
                sequences = sorted(
                    int(row.get("raw_fields", {}).get("list_sequence") or 0)
                    for row in census
                )
                if sequences != list(range(1, len(census) + 1)):
                    raise BusanYeonjeContractError(
                        f"lifelong {office.code} complete sequence has a gap"
                    )
                identities = [_clean(row.get("raw_fields", {}).get("identity")) for row in census]
                if len(identities) != len(set(identities)):
                    raise BusanYeonjeContractError(
                        f"lifelong {office.code} has duplicate identities"
                    )
            if _platform_archive_signature(census_a) != _platform_archive_signature(census_b):
                raise BusanYeonjeContractError(
                    f"lifelong {office.code} complete census changed"
                )
            platform_rows_by_office[office.code] = len(census_a)
            platform_rows.extend(census_a)

        city_rows: list[dict[str, Any]] = []
        city_page_rows: dict[int, list[dict[str, Any]]] = {}
        for page in range(1, city_last + 1):
            if page == 1:
                rows = city_first
            else:
                rows, _ = _parse_city_page(
                    values[("city", "data", page)][0],
                    page=page,
                    expected_last=city_last,
                )
            city_page_rows[page] = rows
            city_rows.extend(rows)
        _parse_city_page(
            values[("city", "sentinel", city_last + 1)][0],
            page=city_last + 1,
            expected_last=city_last,
            sentinel=True,
        )
        for page in city_boundaries:
            rechecked, _ = _parse_city_page(
                values[("city", "recheck", page)][0],
                page=page,
                expected_last=city_last,
            )
            if _city_signature(rechecked) != _city_signature(city_page_rows[page]):
                raise BusanYeonjeContractError(f"Busan city boundary page {page} changed")
        city_ids = [_clean(row.get("provider_course_id")) for row in city_rows]
        if len(city_ids) != len(set(city_ids)):
            raise BusanYeonjeContractError("duplicate Busan city identities")

        local_by_id = {
            _clean(row.get("raw_fields", {}).get("source_identity")): row
            for row in local_rows
        }
        local_by_semantic: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
        for row in local_rows:
            local_by_semantic.setdefault(_platform_semantic_key(row), []).append(row)
        if any(len(rows) != 1 for rows in local_by_semantic.values()):
            raise BusanYeonjeContractError("district immutable duplicate key is ambiguous")

        external_rows = 0
        list_only_rows = 0
        external_duplicates = 0
        list_only_duplicates = 0
        matched_local_ids: list[str] = []
        native_rows: list[dict[str, Any]] = []
        for row in platform_rows:
            raw = row.get("raw_fields", {})
            kind = _clean(raw.get("identity_kind"))
            if kind == "external":
                external_rows += 1
                local_id = _clean(raw.get("external_local_identity"))
                owner = local_by_id.get(local_id)
                if owner is None or _platform_semantic_key(row) != _platform_semantic_key(owner):
                    raise BusanYeonjeContractError(
                        f"lifelong external lecIdx {local_id or '?'} is not an exact local duplicate"
                    )
                matched_local_ids.append(local_id)
                external_duplicates += 1
            elif kind == "list_only_semantic_v1":
                list_only_rows += 1
                owners = local_by_semantic.get(_platform_semantic_key(row), [])
                if len(owners) != 1:
                    raise BusanYeonjeContractError(
                        "lifelong list-only row is not a unique exact local duplicate"
                    )
                matched_local_ids.append(
                    _clean(owners[0].get("raw_fields", {}).get("source_identity"))
                )
                list_only_duplicates += 1
            elif kind == "internal":
                native_rows.append(_platform_native_row(row))
            else:
                raise BusanYeonjeContractError("unknown lifelong identity kind")
        if len(matched_local_ids) != len(set(matched_local_ids)):
            raise BusanYeonjeContractError("lifelong republications duplicate a local identity")

        unique_rows = [*local_rows, *native_rows, *city_rows]
        provider_ids = [_clean(row.get("provider_course_id")) for row in unique_rows]
        if len(provider_ids) != len(set(provider_ids)):
            raise BusanYeonjeContractError("atomic owner has duplicate provider course IDs")

        def current(row: Mapping[str, Any]) -> bool:
            try:
                is_current = date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            except ValueError as exc:
                raise BusanYeonjeContractError("invalid current-row end date") from exc
            raw = row.get("raw_fields", {})
            if is_current and (
                raw.get("source_reversed_education_period")
                or raw.get("source_reversed_application_period")
            ):
                raise BusanYeonjeContractError(
                    "current lifelong row has a reversed source period"
                )
            return is_current

        local_current = [row for row in local_rows if current(row)]
        native_current = [row for row in native_rows if current(row)]
        city_current = [row for row in city_rows if current(row)]
        current_rows = [*local_current, *native_current, *city_current]
        required_details = len(current_rows)
        meta.update(
            {
                "district_source_rows": len(local_rows),
                "district_data_pages": local_last,
                "district_current_count": len(local_current),
                "platform_source_rows": len(platform_rows),
                "platform_rows_by_office": platform_rows_by_office,
                "platform_external_rows": external_rows,
                "platform_list_only_rows": list_only_rows,
                "platform_external_duplicate_rows": external_duplicates,
                "platform_list_only_duplicate_rows": list_only_duplicates,
                "platform_native_rows": len(native_rows),
                "platform_native_current_count": len(native_current),
                "platform_semantic_censuses": 2 * len(offices),
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "source_total": len(local_rows) + len(platform_rows) + len(city_rows),
                "unique_education_source_rows": len(unique_rows),
                "duplicate_source_identity_count": len(matched_local_ids),
                "current_source_count": required_details,
                "expired_count": len(unique_rows) - required_details,
                "required_detail_requests": required_details,
            }
        )
        if required_details > allowed_details:
            meta["source_cap_reached"] = True
            raise BusanYeonjeContractError(
                f"detail_limit cap allows {allowed_details} of {required_details} required details"
            )
        if required_list_requests + required_details > allowed_requests:
            meta["source_cap_reached"] = True
            raise BusanYeonjeContractError(
                f"max_requests cap allows {allowed_requests} of {required_list_requests + required_details} required requests"
            )

        detail_items: list[tuple[Any, str, Probe]] = []
        for row in local_current:
            detail_items.append(
                (
                    ("local", _clean(row.get("provider_course_id"))),
                    _clean(row.get("raw_url")),
                    _probe_local_detail,
                )
            )
        for row in native_current:
            detail_items.append(
                (
                    ("platform", _clean(row.get("provider_course_id"))),
                    _clean(row.get("raw_url")),
                    _probe_platform_detail,
                )
            )
        for row in city_current:
            detail_items.append(
                (
                    ("city", _clean(row.get("provider_course_id"))),
                    _clean(row.get("raw_url")),
                    _probe_city_detail,
                )
            )
        details = _fetch_many(
            detail_items,
            fetcher=fetch,
            session_factory=sessions,
            timeout=request_timeout,
            max_workers=workers,
            sleeper=sleeper,
            budget=budget,
        )
        meta["network_retry_count"] += details.retries
        meta["detail_attempts"] = len(detail_items)
        if details.errors:
            raise BusanYeonjeContractError("; ".join(details.errors))

        verified: list[dict[str, Any]] = []
        for row in current_rows:
            provider_id = _clean(row.get("provider_course_id"))
            catalog = _clean(row.get("raw_fields", {}).get("source_catalog"))
            if catalog == "yeonje_complete_lifelong_catalogue":
                key = ("local", provider_id)
                parser = _parse_local_detail
            elif catalog == "busan_lifelong_yeonje_native":
                key = ("platform", provider_id)
                parser = _parse_platform_detail
            elif catalog == "busan_reserve_yeonje_resident_councils":
                key = ("city", provider_id)
                parser = _parse_city_detail
            else:
                raise BusanYeonjeContractError("unknown current-row source catalogue")
            value = details.values.get(key)
            if value is None:
                raise BusanYeonjeContractError(f"missing current detail {provider_id}")
            verified.append(parser(value[0], value[1], row))

        deduped = list(dedupe(verified))
        if len(deduped) != len(verified):
            raise BusanYeonjeContractError("dedupe changed atomic row count")
        deduped_ids = [_clean(row.get("provider_course_id")) for row in deduped]
        if len(deduped_ids) != len(set(deduped_ids)) or set(deduped_ids) != {
            _clean(row.get("provider_course_id")) for row in verified
        }:
            raise BusanYeonjeContractError("dedupe changed atomic identities")
        if not all(row.get("raw_fields", {}).get("detail_verified") for row in deduped):
            raise BusanYeonjeContractError("not every current detail was verified")

        meta.update(
            {
                "pages": required_list_requests + len(details.values),
                "list_requests": required_list_requests,
                "detail_pages": len(details.values),
                "network_requests": budget.count,
                "returned_count": len(deduped),
                "application_control_count": sum(
                    1 for row in deduped if row.get("reservation_available")
                ),
                "offline_application_count": sum(
                    1 for row in deduped if row.get("application_type") == "OFFLINE_APPLY"
                ),
                "status_counts": dict(
                    sorted(Counter(_clean(row.get("status")) for row in deduped).items())
                ),
                "snapshot_complete": True,
                "atomic_union_complete": True,
                "configured_collection_error": "",
            }
        )
        return deduped, BUSAN_YEONJE_PARSER, meta
    except Exception as exc:
        return _fail(meta, budget, f"{type(exc).__name__}: {_clean(exc)}")


collect = collect_busan_yeonje_education


__all__ = [
    "BUSAN_CITY_RESIDENT_OFFICE",
    "BUSAN_CITY_YEONJE_GUGUN",
    "BUSAN_CITY_YEONJE_PROVIDER",
    "BUSAN_CITY_YEONJE_URL",
    "BUSAN_LIFELONG_DEDICATED_OWNERSHIP",
    "BUSAN_LIFELONG_EXPECTED_OWNERSHIP",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_LIFELONG_YEONJE_OFFICES",
    "BUSAN_YEONJE_CANDIDATE_IDS",
    "BUSAN_YEONJE_CANONICAL_URL",
    "BUSAN_YEONJE_DISCOVERY_AUDIT",
    "BUSAN_YEONJE_MUNICIPALITY_CODE",
    "BUSAN_YEONJE_MUNICIPALITY_NAME",
    "BUSAN_YEONJE_OWNER_BOUNDARY_AUDIT",
    "BUSAN_YEONJE_OWNERSHIP_SCOPE",
    "BUSAN_YEONJE_PARSER",
    "BUSAN_YEONJE_PROVIDER",
    "BUSAN_YEONJE_REGISTERED_URL",
    "BusanYeonjeContractError",
    "busan_yeonje_city_detail_url",
    "busan_yeonje_city_list_url",
    "busan_yeonje_detail_url",
    "busan_yeonje_lifelong_detail_url",
    "busan_yeonje_lifelong_list_url",
    "busan_yeonje_list_url",
    "canonical_busan_yeonje_course_identity",
    "collect",
    "collect_busan_yeonje_education",
    "is_busan_yeonje_education_target",
    "is_target",
]
