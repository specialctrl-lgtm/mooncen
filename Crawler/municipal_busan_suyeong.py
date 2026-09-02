"""Atomic education collector for Busan Suyeong-gu's official ledgers.

The two configured Suyeong lifelong-learning URLs are landing/information
pages.  The complete district owner is the unfiltered ``BBS_0000152``
``전체프로그램`` ledger in Suyeong's integrated-reservation site.  Its
category menus are subsets of that ledger and are not collected again.

The same atomic snapshot also audits the Busan integrated-reservation
resident-council partition fixed to ``srchGugun=12`` and
``srchResveInsttCd=33``, plus 부산평생학습플랫폼 office
``OFFICE_00002661``.  Exact platform links back to a district ``dataSid`` are
suppressed as duplicates; native ``LEARNING_*`` rows remain independent.

Every declared page, the immediate empty sentinel, stable first/final
boundaries, and every current/future safe detail are mandatory.  Any contract
failure discards the full union.  Instructor/contact values, attachment
names, free-form descriptions, public applicant-table values, application
forms, account/history pages, and identity-verification routes are never read
or fetched.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import hashlib
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong


BUSAN_SUYEONG_PROVIDER = "MUNI_WWW_SUYEONG_GO_KR_41E9DDEB"
BUSAN_SUYEONG_INFORMATION_PROVIDER = "MUNI_WWW_SUYEONG_GO_KR_4A5037DF"
BUSAN_CITY_SUYEONG_PROVIDER = "MUNI_RESERVE_BUSAN_GO_KR_3A0E6D4C"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER

BUSAN_SUYEONG_MUNICIPALITY_CODE = "2650000000"
BUSAN_SUYEONG_MUNICIPALITY_NAME = "부산광역시 수영구"
BUSAN_SUYEONG_HOST = "www.suyeong.go.kr"
BUSAN_SUYEONG_HOME_PATH = "/lll/index.suyeong"
BUSAN_SUYEONG_HOME_URL = f"https://{BUSAN_SUYEONG_HOST}{BUSAN_SUYEONG_HOME_PATH}"
BUSAN_SUYEONG_INFORMATION_MENU = "DOM_000001701002000000"
BUSAN_SUYEONG_INFORMATION_URL = BUSAN_SUYEONG_HOME_URL + "?" + urlencode({"menuCd": BUSAN_SUYEONG_INFORMATION_MENU})
BUSAN_SUYEONG_BOARD_ID = "BBS_0000152"
BUSAN_SUYEONG_MENU = "DOM_000001801001000000"
BUSAN_SUYEONG_CONTENTS_SID = "399"
BUSAN_SUYEONG_LIST_PATH = "/board/list.suyeong"
BUSAN_SUYEONG_DETAIL_PATH = "/reserve/board/view.suyeong"
BUSAN_SUYEONG_APPLICATION_PATH = "/reserve/index.suyeong"
BUSAN_SUYEONG_APPLICATION_WRITE_PATH = "/reserve/board/write.suyeong"
BUSAN_SUYEONG_LOGIN_MENU = "DOM_000000107000000000"
BUSAN_SUYEONG_CANONICAL_URL = f"https://{BUSAN_SUYEONG_HOST}{BUSAN_SUYEONG_LIST_PATH}?" + urlencode(
    (("boardId", BUSAN_SUYEONG_BOARD_ID), ("menuCd", BUSAN_SUYEONG_MENU))
)
BUSAN_SUYEONG_URL = BUSAN_SUYEONG_CANONICAL_URL
BUSAN_SUYEONG_PAGE_SIZE = 9

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_SUYEONG_GUGUN = "12"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_SUYEONG_URL = f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
    (
        ("curPage", "1"),
        ("srchGugun", BUSAN_CITY_SUYEONG_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    )
)

BUSAN_LIFELONG_SUYEONG_OFFICE = "OFFICE_00002661"
BUSAN_LIFELONG_SUYEONG_OFFICE_NAME = "수영구청"
BUSAN_LIFELONG_SUYEONG_EXPECTED_OWNERSHIP = "duplicate_dedicated_suyeong_owner"
BUSAN_LIFELONG_PAGE_SIZE = 1000
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_OFFICE_URL = _lifelong.BUSAN_LIFELONG_URL

BUSAN_SUYEONG_FETCH_ATTEMPTS = 3
BUSAN_SUYEONG_MAX_WORKERS = 12
BUSAN_SUYEONG_MAX_HTML_BYTES = 12_000_000
BUSAN_SUYEONG_PARSER = (
    "busan_suyeong_bbs0000152_unfiltered_all_education_complete_pages+"
    "empty_sentinel+stable_first_last+busan_reserve_gugun12_office33_complete+"
    "empty_sentinel+stable_first_last+lifelong_office00002661_pageunit1000_"
    "two_stable_censuses+external_datasid_duplicate_suppression+native_"
    "learning_current_details+all_current_safe_details+identity_bound_apply_"
    "no_form_fetch+pii_allowlist+atomic_three_ledger_snapshot"
)
BUSAN_SUYEONG_OWNERSHIP_SCOPE = (
    "suyeong_complete_integrated_reservation_education_resident_councils_and_native_lifelong_platform_courses"
)

BUSAN_SUYEONG_CANDIDATE_IDS: Mapping[str, str] = {
    "canonical_complete_education_ledger": "MUNI_IR_9276D4CB08F2",
    "registered_lifelong_home": "MUNI_IR_3EA7894865CF",
    "lifelong_information_page": "MUNI_IR_63794D8CB3BB",
    "busan_resident_councils": "MUNI_IR_B21AE03DCD52",
    "busan_lifelong_federation": "MUNI_IR_4332B8F8A6D7",
    "wrong_municipality_museum_detail": "MUNI_IR_2BA97ED12CEB",
}

BUSAN_SUYEONG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_SUYEONG_PROVIDER: {
        "decision": "retain_provider_retarget_home_to_complete_education_owner",
        "candidate_id": BUSAN_SUYEONG_CANDIDATE_IDS["registered_lifelong_home"],
        "registered_url": BUSAN_SUYEONG_HOME_URL,
        "canonical_candidate_id": BUSAN_SUYEONG_CANDIDATE_IDS["canonical_complete_education_ledger"],
        "canonical_url": BUSAN_SUYEONG_CANONICAL_URL,
        "identity_rule": "BBS_0000152 plus numeric dataSid",
    },
    BUSAN_SUYEONG_INFORMATION_PROVIDER: {
        "decision": "duplicate_information_page_of_complete_owner",
        "candidate_id": BUSAN_SUYEONG_CANDIDATE_IDS["lifelong_information_page"],
        "url": BUSAN_SUYEONG_INFORMATION_URL,
    },
    BUSAN_CITY_SUYEONG_PROVIDER: {
        "decision": "merge_exact_suyeong_resident_partition_into_atomic_owner",
        "candidate_id": BUSAN_SUYEONG_CANDIDATE_IDS["busan_resident_councils"],
        "url": BUSAN_CITY_SUYEONG_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_SUYEONG_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
        "identity_rule": "resveGroupSn plus progrmSn",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_exact_external_datasid_keep_native_learning_ids",
        "candidate_id": BUSAN_SUYEONG_CANDIDATE_IDS["busan_lifelong_federation"],
        "url": BUSAN_LIFELONG_OFFICE_URL,
        "office_code": BUSAN_LIFELONG_SUYEONG_OFFICE,
        "identity_rule": (
            "external Suyeong BBS_0000152/dataSid belongs to district owner; LEARNING_* remains independent"
        ),
    },
    "WRONG_MUNICIPALITY_SEARCH_DETAILS": {
        "decision": "exclude",
        "candidate_ids": (BUSAN_SUYEONG_CANDIDATE_IDS["wrong_municipality_museum_detail"],),
        "reason": "single Busan institution detail is not a Suyeong-gu ledger",
    },
    "PRIVATE_AND_NON_EDUCATION_BOUNDARY": {
        "decision": "never_fetch",
        "excluded": (
            "facility rental, CCTV tours, banners, account/history, identity "
            "verification, application forms, applicant actions, attachments, "
            "and free-form detail routes"
        ),
    },
}

BUSAN_SUYEONG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "registered_url": BUSAN_SUYEONG_HOME_URL,
    "registered_response": "HTTP 200 landing page without a complete ledger",
    "information_url": BUSAN_SUYEONG_INFORMATION_URL,
    "information_response": "HTTP 200 programme information page",
    "canonical_url": BUSAN_SUYEONG_CANONICAL_URL,
    "district_rows": 1730,
    "district_data_pages": 193,
    "district_page_counts": {"1-192": 9, "193": 2},
    "district_sentinel_page": 194,
    "district_source_status_counts": {
        "교육마감": 1655,
        "교육중": 29,
        "접수마감": 21,
        "접수중": 14,
        "대기중": 11,
    },
    "district_current_rows": 77,
    "district_current_source_status_counts": {
        "교육중": 29,
        "접수마감": 21,
        "접수중": 14,
        "대기중": 11,
        "교육마감": 2,
    },
    "platform_office": BUSAN_LIFELONG_SUYEONG_OFFICE,
    "platform_rows": 108,
    "platform_external_rows": 100,
    "platform_external_unique_datasid": 100,
    "platform_native_rows": 8,
    "platform_current_external_duplicates": 11,
    "platform_native_current_rows": 5,
    "resident_rows": 35,
    "resident_data_pages": 4,
    "resident_page_counts": {"1-3": 10, "4": 5},
    "resident_sentinel_page": 5,
    "resident_current_rows": 35,
    "resident_source_status_counts": {"접수마감": 34, "접수중": 1},
    "resident_branch_counts": {
        "수영구 망미2동 주민자치회": 14,
        "수영구 광안1동 주민자치회": 11,
        "수영구 광안4동 주민자치회": 10,
    },
    "source_rows": 1873,
    "unique_education_source_rows": 1773,
    "atomic_current_rows": 117,
    "atomic_required_list_requests": 206,
    "atomic_required_detail_requests": 117,
    "atomic_required_requests_without_retries": 323,
}

BUSAN_SUYEONG_PII_FIELDS_NEVER_READ = (
    "district instructor values and public applicant-table values",
    "district attachment names/content and free-form description",
    "Busan city inquiry/contact and attachment values",
    "platform instructor/contact/enrolment and free-form values",
    "application forms, account/history and identity-verification pages",
)


class BusanSuyeongContractError(ValueError):
    """Raised when an audited Suyeong-gu source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport, response, or status-200 error-page failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Parser = Callable[[BeautifulSoup, str], Any]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_BOARD_PAGE_RE = re.compile(
    r"^총게시물\s*:\s*([\d,]+)\s*건\s*/\s*페이지\s*:\s*"
    r"\d+\s*/\s*(\d+)$"
)
_TITLE_CATEGORY_RE = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*"
    r"(\d{1,2})(?:\.)?(?!\d)"
)
_DETAIL_TITLE_RE = re.compile(r"^\[\s*20\d{2}년[^\]]*\]\s*(.+)$")
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

_LOCAL_LIST_TITLE = "전체프로그램의 목록 |교육/강좌 |부산광역시 수영구통합예약"
_LOCAL_DETAIL_TITLE = "전체프로그램의 내용 |교육/강좌 |부산광역시 수영구통합예약"
_LOCAL_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
    "교육중": "OPEN",
    "교육마감": "CLOSED",
}
_LOCAL_STATUS_CLASS = {
    "대기중": "state_2",
    "접수중": "state_1",
    "접수마감": "state_3",
    "교육중": "state_4",
    "교육마감": "state_5",
}
_LOCAL_LIST_APPLICATION_LABELS = frozenset({"강좌신청하기", "접수하기"})
_LOCAL_DETAIL_STATUS_CLASS = {
    "대기중": "st1",
    "접수중": "st2",
    "접수마감": "st3",
}
_LOCAL_DETAIL_STATUS_MAP = {
    "대기중": "SCHEDULED",
    "접수중": "OPEN",
    "접수마감": "CLOSED",
}
_LOCAL_LIST_LABELS = (
    "교육기간",
    "교육장소",
    "교육시간",
    "강의요일",
    "신청방법",
)
_LOCAL_OPTIONAL_PRIVATE_LABEL = "강사명"
_LOCAL_DETAIL_ALLOWED_LABELS = frozenset(
    {
        "접수기간",
        "교육기간",
        "시간(요일)",
        "대상구분",
        "수강료",
        "재료비",
        "준비물",
        "강사명",
        "대상인원",
        "신청현황",
    }
)
_LOCAL_DETAIL_SAFE_LABELS = frozenset({"접수기간", "교육기간", "시간(요일)", "대상구분", "수강료", "재료비"})
_LOCAL_DETAIL_REQUIRED_LABELS = frozenset(
    {
        "접수기간",
        "교육기간",
        "시간(요일)",
        "대상구분",
        "수강료",
        "대상인원",
        "신청현황",
    }
)
_AUDITED_MISSING_LOCAL_PERIODS: Mapping[str, Mapping[str, str]] = {
    "284930": {"application": "2024-12-16 ~"},
    "271991": {"application": "~", "education": "~"},
    "216435": {"application": "~", "education": "~"},
    "215761": {"application": "~", "education": "~"},
    "210143": {"application": "~", "education": "~"},
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
_CITY_DETAIL_SAFE_LABELS = frozenset(_CITY_DETAIL_REQUIRED_LABELS) - {"문의전화"}
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
        raise BusanSuyeongContractError(f"expected one {label}, found {len(found)}")
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
        raise BusanSuyeongContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanSuyeongContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanSuyeongContractError(f"{label} must be positive")
    return result


def _normal_path(value: Any) -> str:
    return re.sub(r"/{2,}", "/", str(value or "/"))


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
    return f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}" + (f"?{query}" if query else "")


def is_busan_suyeong_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_SUYEONG_PROVIDER:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(BUSAN_SUYEONG_HOME_URL),
        _compare_url(BUSAN_SUYEONG_CANONICAL_URL),
    }


is_target = is_busan_suyeong_education_target


def busan_suyeong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "district page")
    return f"https://{BUSAN_SUYEONG_HOST}{BUSAN_SUYEONG_LIST_PATH}?" + urlencode(
        (
            ("boardId", BUSAN_SUYEONG_BOARD_ID),
            ("menuCd", BUSAN_SUYEONG_MENU),
            ("nowPage", value),
        )
    )


def busan_suyeong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise BusanSuyeongContractError("invalid Suyeong dataSid")
    return f"https://{BUSAN_SUYEONG_HOST}{BUSAN_SUYEONG_DETAIL_PATH}?" + urlencode(
        (
            ("boardId", BUSAN_SUYEONG_BOARD_ID),
            ("startPage", "1"),
            ("menuCd", BUSAN_SUYEONG_MENU),
            ("dataSid", value),
        )
    )


def busan_suyeong_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", value),
            ("srchGugun", BUSAN_CITY_SUYEONG_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_suyeong_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not _IDENTITY_RE.fullmatch(group) or not _IDENTITY_RE.fullmatch(program):
        raise BusanSuyeongContractError("invalid Busan city course identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def busan_suyeong_lifelong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_SUYEONG_OFFICE, value)
    payload["pageUnit"] = str(BUSAN_LIFELONG_PAGE_SIZE)
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_suyeong_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanSuyeongContractError("invalid native lifelong identity")
    return _lifelong.busan_lifelong_detail_url(value)


def canonical_busan_suyeong_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_SUYEONG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_SUYEONG_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"boardId", "startPage", "menuCd", "dataSid"}
        or query.get("boardId") != [BUSAN_SUYEONG_BOARD_ID]
        or query.get("startPage") != ["1"]
        or query.get("menuCd") != [BUSAN_SUYEONG_MENU]
    ):
        return ""
    identity = _query_one(query, "dataSid")
    return identity if _IDENTITY_RE.fullmatch(identity) else ""


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanSuyeongContractError("invalid source date") from exc
    return result


def _date_pair(value: Any, label: str) -> tuple[str, str]:
    found = _dates(value)
    if len(found) != 2:
        raise BusanSuyeongContractError(f"{label} is not an exact date range")
    if found[1] < found[0]:
        raise BusanSuyeongContractError(f"{label} is reversed")
    return found[0].isoformat(), found[1].isoformat()


def _audited_optional_range(value: Any, *, identity: str, kind: str) -> tuple[str, str, bool]:
    raw = _clean(value)
    try:
        start, end = _date_pair(raw, f"district {kind} period")
        return start, end, False
    except BusanSuyeongContractError:
        expected = _AUDITED_MISSING_LOCAL_PERIODS.get(identity, {}).get(kind)
        if expected != raw:
            raise
        return "", "", True


def _direct_after(label: Tag) -> str:
    parts: list[str] = []
    for sibling in label.next_siblings:
        if isinstance(sibling, NavigableString):
            value = _clean(sibling)
            if value:
                parts.append(value)
        elif isinstance(sibling, Tag) and sibling.name == "br":
            continue
        elif isinstance(sibling, Tag) and sibling.name == "li":
            break
        elif isinstance(sibling, Tag):
            value = _text(sibling)
            if value:
                parts.append(value)
    return _clean(" ".join(parts))


def _application_url(value: Any, identity: str) -> str:
    resolved = urljoin(BUSAN_SUYEONG_CANONICAL_URL, _clean(value))
    parsed = urlparse(resolved)
    query = parse_qs(parsed.query, keep_blank_values=True)
    safe_origin = (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_SUYEONG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_SUYEONG_APPLICATION_PATH
        or parsed.params
        or parsed.fragment
    )
    if safe_origin:
        raise BusanSuyeongContractError("unsafe district application control")

    if set(query) == {"menuCd", "rmenuCd", "INTEDUNUM"}:
        if query.get("rmenuCd") != [BUSAN_SUYEONG_MENU] or query.get("INTEDUNUM") != [identity]:
            raise BusanSuyeongContractError("unsafe district application control")
        application_menu = _query_one(query, "menuCd")
        if not re.fullmatch(r"DOM_000001801\d{6}001", application_menu):
            raise BusanSuyeongContractError("district application menu left education")
        return resolved

    if (
        set(query) != {"menuCd", "forwardUrl", "returnUrl"}
        or query.get("menuCd") != [BUSAN_SUYEONG_LOGIN_MENU]
        or query.get("forwardUrl") != query.get("returnUrl")
    ):
        raise BusanSuyeongContractError("unsafe district application control")
    nested = urlparse(_query_one(query, "forwardUrl"))
    nested_query = parse_qs(unquote(nested.query), keep_blank_values=True)
    if (
        nested.scheme.lower() != "https"
        or (nested.hostname or "").rstrip(".").lower() != BUSAN_SUYEONG_HOST
        or nested.port is not None
        or nested.username
        or nested.password
        or _normal_path(nested.path) != BUSAN_SUYEONG_APPLICATION_WRITE_PATH
        or nested.params
        or nested.fragment
        or set(nested_query) != {"boardId", "menuCd", "INTNUM"}
        or nested_query.get("boardId") != [BUSAN_SUYEONG_BOARD_ID]
        or nested_query.get("menuCd") != [BUSAN_SUYEONG_MENU]
        or nested_query.get("INTNUM") != [identity]
    ):
        raise BusanSuyeongContractError("unsafe district login application control")
    return resolved


def _detail_identity(value: Any) -> str:
    parsed = urlparse(urljoin(BUSAN_SUYEONG_CANONICAL_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_SUYEONG_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != BUSAN_SUYEONG_DETAIL_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"boardId", "startPage", "menuCd", "dataSid"}
        or query.get("boardId") != [BUSAN_SUYEONG_BOARD_ID]
        or query.get("startPage") != ["1"]
        or query.get("menuCd") != [BUSAN_SUYEONG_MENU]
    ):
        raise BusanSuyeongContractError("unsafe district detail link")
    identity = _query_one(query, "dataSid")
    if not _IDENTITY_RE.fullmatch(identity):
        raise BusanSuyeongContractError("invalid district dataSid")
    return identity


def _local_list_contract(soup: BeautifulSoup, *, page: int) -> tuple[int, int, Tag]:
    if _text(_one(soup.select("title"), "district list title")) != _LOCAL_LIST_TITLE:
        raise BusanSuyeongContractError("district list title changed")
    form = _one(soup.select("form.rfc_bbs_searchForm"), "district search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != BUSAN_SUYEONG_LIST_PATH
    ):
        raise BusanSuyeongContractError("district search form changed")
    required = {
        "boardId": BUSAN_SUYEONG_BOARD_ID,
        "menuCd": BUSAN_SUYEONG_MENU,
        "contentsSid": BUSAN_SUYEONG_CONTENTS_SID,
        "startPage": "1",
    }
    for name, expected in required.items():
        fields = form.select(f"input[name='{name}']")
        if len(fields) != 1 or _clean(fields[0].get("value")) != expected:
            raise BusanSuyeongContractError(f"district form {name} changed")
    page_text = _text(_one(soup.select("p.boardPage"), "district board total"))
    match = _BOARD_PAGE_RE.fullmatch(page_text)
    if not match:
        raise BusanSuyeongContractError("district total/page marker changed")
    total = int(match.group(1).replace(",", ""))
    last = int(match.group(2))
    if total < 1 or last != math.ceil(total / BUSAN_SUYEONG_PAGE_SIZE):
        raise BusanSuyeongContractError("district declared pagination changed")
    root = _one(soup.select("div.sub_reserve_box"), "district course-list root")
    current = soup.select("div.page > a.on[href]")
    if page <= last:
        marker = _one(current, "district active page marker")
        query = parse_qs(
            urlparse(urljoin(BUSAN_SUYEONG_CANONICAL_URL, marker.get("href"))).query,
            keep_blank_values=True,
        )
        if _query_one(query, "nowPage") != str(page):
            raise BusanSuyeongContractError("district active page differs from request")
    elif page == last + 1:
        if current:
            raise BusanSuyeongContractError("district sentinel gained active marker")
    else:
        raise BusanSuyeongContractError("district request passed sentinel")
    return total, last, root


def _parse_local_page(
    soup: BeautifulSoup,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last, root = _local_list_contract(soup, page=page)
    if expected_total is not None and total != expected_total:
        raise BusanSuyeongContractError("district declared total changed")
    if expected_last is not None and last != expected_last:
        raise BusanSuyeongContractError("district displayed last page changed")
    cards = root.select(":scope > ul > li > div.list_box")
    expected_count = (
        0
        if page == last + 1
        else BUSAN_SUYEONG_PAGE_SIZE
        if page < last
        else total - BUSAN_SUYEONG_PAGE_SIZE * (last - 1)
    )
    if len(cards) != expected_count:
        raise BusanSuyeongContractError(f"district page {page} expected {expected_count} rows, found {len(cards)}")
    rows: list[dict[str, Any]] = []
    for position, card in enumerate(cards, 1):
        status_node = _one(
            card.select(":scope > div.cate_box > span.cate_1"),
            "district source status",
        )
        source_status = _text(status_node)
        if source_status not in _LOCAL_STATUS_MAP:
            raise BusanSuyeongContractError("unknown district source status")
        if _LOCAL_STATUS_CLASS[source_status] not in set(card.get("class", [])):
            raise BusanSuyeongContractError("district status class changed")
        link = _one(card.select(":scope > h5 > a[href]"), "district detail link")
        identity = _detail_identity(link.get("href"))
        full_title = _text(link)
        title_match = _TITLE_CATEGORY_RE.fullmatch(full_title)
        if title_match:
            category = _clean(title_match.group(1))
            title = _clean(title_match.group(2))
            category_in_title = True
        else:
            # Thirteen historical rows predate the bracketed category prefix.
            # They still belong to the unfiltered education ledger, but must
            # not be guessed into one of its modern category partitions.
            category = "미분류 교육프로그램"
            title = full_title
            category_in_title = False
        if not category or not title:
            raise BusanSuyeongContractError("empty district title/category")
        apply_raw = _text(_one(card.select(":scope > span.date"), "district application period"))
        apply_start, apply_end, audited_missing_apply = _audited_optional_range(
            apply_raw, identity=identity, kind="application"
        )
        definitions = card.find_all("dl", recursive=False)
        labels: list[str] = []
        safe: dict[str, str] = {}
        instructor_seen = False
        for definition in definitions:
            heading = _one(definition.find_all("dt", recursive=False), "district list label")
            value = _one(definition.find_all("dd", recursive=False), "district list value")
            label = _text(heading)
            if label in labels:
                raise BusanSuyeongContractError("duplicate district list label")
            labels.append(label)
            if label == _LOCAL_OPTIONAL_PRIVATE_LABEL:
                instructor_seen = True
                # The instructor value is deliberately not converted to text.
                continue
            if label not in _LOCAL_LIST_LABELS:
                raise BusanSuyeongContractError(f"unknown district list field {label!r}")
            safe[label] = _text(value)
        visible_labels = tuple(label for label in labels if label != _LOCAL_OPTIONAL_PRIVATE_LABEL)
        if visible_labels != _LOCAL_LIST_LABELS:
            raise BusanSuyeongContractError("district list field contract changed")
        start, end, audited_missing_education = _audited_optional_range(
            safe["교육기간"], identity=identity, kind="education"
        )
        control_root = _one(card.select(":scope > div.more"), "district application marker")
        controls = control_root.select(":scope > a[href]")
        application_url = ""
        if controls:
            control = _one(controls, "district application control")
            if _text(control) not in _LOCAL_LIST_APPLICATION_LABELS or source_status not in {"접수중", "교육중"}:
                raise BusanSuyeongContractError("district application control/status changed")
            application_url = _application_url(control.get("href"), identity)
        else:
            marker = _text(control_root)
            expected_marker = {
                "대기중": "대기중입니다",
                "접수마감": "접수가 마감되었습니다",
                "교육중": "접수가 마감되었습니다",
                "교육마감": "접수가 마감되었습니다",
            }.get(source_status)
            if expected_marker is None or marker != expected_marker:
                raise BusanSuyeongContractError("district unavailable marker changed")
        raw_url = busan_suyeong_detail_url(identity)
        rows.append(
            {
                "provider": BUSAN_SUYEONG_PROVIDER,
                "provider_course_id": f"{BUSAN_SUYEONG_PROVIDER}:district:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": category,
                "branch_code": category,
                "preserve_branch": True,
                "category": category,
                "program_type": "교육/강좌",
                "raw_url": raw_url,
                "application_url": application_url,
                "application_type": ("ONLINE_RESERVATION" if application_url else "INFO_ONLY"),
                "application_method_raw": safe["신청방법"],
                "reservation_available": bool(application_url),
                "status": ("OPEN" if application_url else "SCHEDULED" if source_status == "대기중" else "CLOSED"),
                "fee": "",
                "period": f"{start} ~ {end}" if start and end else safe["교육기간"],
                "start_date": start,
                "end_date": end,
                "apply_period": (f"{apply_start} ~ {apply_end}" if apply_start and apply_end else apply_raw),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": _clean(f"{safe['강의요일']} {safe['교육시간']}"),
                "target": "",
                "capacity_total": None,
                "capacity_current": None,
                "venue_name": safe["교육장소"],
                "provider_organizer": BUSAN_SUYEONG_MUNICIPALITY_NAME,
                "municipality_code": BUSAN_SUYEONG_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_SUYEONG_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "수영구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "parser": BUSAN_SUYEONG_PARSER,
                    "source_catalog": "suyeong_complete_district_education",
                    "source_identity": f"{BUSAN_SUYEONG_BOARD_ID}:{identity}",
                    "source_data_sid": identity,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_category": category,
                    "source_category_prefix_present": category_in_title,
                    "source_application_period": apply_raw,
                    "source_education_period": safe["교육기간"],
                    "instructor_field_present_value_never_read": instructor_seen,
                    "audited_missing_application_period": audited_missing_apply,
                    "audited_missing_education_period": audited_missing_education,
                    "application_control_present": bool(application_url),
                    "application_control_identity_verified": bool(application_url),
                    "detail_verified": False,
                    "attachments_never_read": True,
                    "free_form_detail_never_read": True,
                    "applicant_table_values_never_read": True,
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
                    _clean(row.get("raw_fields", {}).get("source_status")),
                )
                for row in rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def _parse_local_detail(soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_data_sid"))
    if _compare_url(final_url) != _compare_url(busan_suyeong_detail_url(identity)):
        raise BusanSuyeongContractError("district detail response scope changed")
    if _text(_one(soup.select("title"), "district detail title")) != _LOCAL_DETAIL_TITLE:
        raise BusanSuyeongContractError("district detail title changed")
    root = _one(soup.select("div.bbs_vtype.edu"), "district safe detail root")
    information = _one(root.select(":scope > dl.infor"), "district detail information")
    heading = _one(information.select(":scope > dt"), "district detail heading")
    status_node = _one(heading.select(":scope > span.state"), "district detail status")
    detail_status = _text(status_node)
    if detail_status not in _LOCAL_DETAIL_STATUS_MAP or _LOCAL_DETAIL_STATUS_CLASS[detail_status] not in set(
        status_node.get("class", [])
    ):
        raise BusanSuyeongContractError("district detail status changed")
    direct_title = _clean(
        " ".join(_clean(child) for child in heading.children if isinstance(child, NavigableString) and _clean(child))
    )
    title_match = _DETAIL_TITLE_RE.fullmatch(direct_title)
    if not title_match or not _clean(parent.get("title")).endswith(_clean(title_match.group(1))):
        raise BusanSuyeongContractError("district list/detail title mismatch")
    expected_detail_status = {
        "대기중": "대기중",
        "접수중": "접수중",
        "접수마감": "접수마감",
        "교육중": ("접수중" if parent.get("application_url") else "접수마감"),
        "교육마감": "접수마감",
    }[_clean(raw.get("source_status"))]
    if detail_status != expected_detail_status:
        raise BusanSuyeongContractError("district list/detail status mismatch")
    safe_root = _one(information.select(":scope > dd.edu > ul"), "district detail safe fields")
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for item in safe_root.find_all("li", recursive=False):
        label_node = _one(
            item.find_all("span", class_="name", recursive=False),
            "district detail field label",
        )
        label = _text(label_node)
        if label in labels or label not in _LOCAL_DETAIL_ALLOWED_LABELS:
            raise BusanSuyeongContractError(f"unknown or duplicate district detail field {label!r}")
        labels.append(label)
        if label in _LOCAL_DETAIL_SAFE_LABELS:
            safe[label] = _direct_after(label_node)
        else:
            # Instructor, preparation notes, and enrolment values are not read.
            skipped.add(label)
    if not _LOCAL_DETAIL_REQUIRED_LABELS.issubset(labels):
        raise BusanSuyeongContractError("district detail required fields changed")
    detail_start, detail_end = _date_pair(safe["교육기간"], "district detail education period")
    detail_apply_start, detail_apply_end = _date_pair(safe["접수기간"], "district detail application period")
    if (detail_start, detail_end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (detail_apply_start, detail_apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanSuyeongContractError("district list/detail dates mismatch")
    controls = soup.select("#content > div.btn_list2 > span.btnBs > a[href]")
    if len(controls) > 1:
        raise BusanSuyeongContractError("multiple district application controls")
    normalized_status = _LOCAL_DETAIL_STATUS_MAP[detail_status]
    application_url = ""
    if normalized_status == "OPEN":
        control = _one(controls, "district detail application control")
        if _text(control) != "강좌신청하기":
            raise BusanSuyeongContractError("district detail application label changed")
        application_url = _application_url(control.get("href"), identity)
        if _compare_url(application_url) != _compare_url(parent.get("application_url")):
            raise BusanSuyeongContractError("district list/detail application identity mismatch")
    elif controls:
        raise BusanSuyeongContractError("unavailable district detail became actionable")
    target_value = _clean(safe.get("대상구분"))
    fee_value = _clean(safe.get("수강료"))
    result = dict(parent)
    result.update(
        {
            "status": normalized_status,
            "application_url": application_url,
            "application_type": ("ONLINE_RESERVATION" if application_url else "INFO_ONLY"),
            "reservation_available": bool(application_url),
            "target": target_value or "공식 페이지 미기재",
            "fee": fee_value or "공식 페이지 미기재",
            "schedule_raw": safe.get("시간(요일)", ""),
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": detail_status,
        "detail_field_labels": labels,
        "detail_application_control": bool(application_url),
        "target_source_omission": not target_value,
        "fee_source_omission": not fee_value,
        "instructor_value_never_read": "강사명" in skipped,
        "preparation_value_never_read": "준비물" in skipped,
        "enrolment_values_never_read": {"대상인원", "신청현황"}.issubset(skipped),
        "attachments_never_read": True,
        "free_form_detail_never_read": True,
        "applicant_table_values_never_read": True,
        "application_form_fetched": False,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(BUSAN_LIFELONG_SUYEONG_OFFICE)
    if (
        office is None
        or office.name != BUSAN_LIFELONG_SUYEONG_OFFICE_NAME
        or office.municipality_code
        or office.municipality_name
        or office.ownership != BUSAN_LIFELONG_SUYEONG_EXPECTED_OWNERSHIP
    ):
        raise BusanSuyeongContractError("lifelong Suyeong office ownership changed")
    return _lifelong.BusanOffice(
        office.code,
        office.name,
        municipality_code=BUSAN_SUYEONG_MUNICIPALITY_CODE,
        municipality_name=BUSAN_SUYEONG_MUNICIPALITY_NAME,
        ownership=BUSAN_LIFELONG_SUYEONG_EXPECTED_OWNERSHIP,
    )


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
        raise BusanSuyeongContractError("; ".join(errors))
    if expected_last is not None and last != expected_last:
        raise BusanSuyeongContractError("lifelong displayed last page changed")
    if last != 1:
        raise BusanSuyeongContractError("lifelong pageUnit=1000 no longer yields one data page")
    if page == 1:
        sequences = sorted(int(row.get("raw_fields", {}).get("list_sequence") or 0) for row in rows)
        if sequences != list(range(1, len(rows) + 1)):
            raise BusanSuyeongContractError("lifelong complete sequence has a gap")
    elif page == 2:
        if rows:
            raise BusanSuyeongContractError("lifelong sentinel is not empty")
    else:
        raise BusanSuyeongContractError("lifelong request passed sentinel")
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
        raise BusanSuyeongContractError("invalid native lifelong identity")
    result.update(
        {
            "provider": BUSAN_SUYEONG_PROVIDER,
            "provider_course_id": f"{BUSAN_SUYEONG_PROVIDER}:lifelong:{identity}",
            "prefer_incoming_provider_course_id": True,
            "municipality_code": BUSAN_SUYEONG_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_SUYEONG_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "수영구",
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
        "parser": BUSAN_SUYEONG_PARSER,
        "source_catalog": "busan_lifelong_suyeong_native",
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
    allowed = set(_PLATFORM_DETAIL_REQUIRED_LABELS) | set(_PLATFORM_DETAIL_OPTIONAL_LABELS)
    for definition in soup.select("div.form_group dl"):
        heading = _one(definition.find_all("dt", recursive=False), "lifelong detail label")
        value = _one(definition.find_all("dd", recursive=False), "lifelong detail value")
        label = _text(heading)
        if not label or label in labels or label not in allowed:
            raise BusanSuyeongContractError(f"unknown or duplicate lifelong detail field {label!r}")
        labels.append(label)
        if label in _PLATFORM_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    without_optional = [label for label in labels if label not in _PLATFORM_DETAIL_OPTIONAL_LABELS]
    if tuple(without_optional) != _PLATFORM_DETAIL_REQUIRED_LABELS:
        raise BusanSuyeongContractError("lifelong detail field order changed")
    expected_skipped = (
        set(_PLATFORM_DETAIL_REQUIRED_LABELS) | (set(labels) & set(_PLATFORM_DETAIL_OPTIONAL_LABELS))
    ) - set(_PLATFORM_DETAIL_SAFE_LABELS)
    if skipped != expected_skipped:
        raise BusanSuyeongContractError("lifelong private field boundary changed")
    return tuple(labels), safe, skipped


def _parse_platform_detail(soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if _compare_url(final_url) != _compare_url(busan_suyeong_lifelong_detail_url(identity)):
        raise BusanSuyeongContractError("lifelong detail response scope changed")
    form = _one(soup.select("form#learningVO[name='learningVO']"), "lifelong detail form")
    action = urlparse(urljoin(final_url, _clean(form.get("action"))))
    if (
        _clean(form.get("method")).casefold() != "post"
        or action.path != BUSAN_LIFELONG_DETAIL_PATH
        or parse_qs(action.query, keep_blank_values=True).get("lng_id") != [identity]
    ):
        raise BusanSuyeongContractError("lifelong detail form changed")
    identity_fields = {_clean(node.get("value")) for node in form.select("input[name='lng_id']")}
    office_fields = {_clean(node.get("value")) for node in form.select("input[name='inst_id']")}
    if identity_fields != {identity} or office_fields != {BUSAN_LIFELONG_SUYEONG_OFFICE}:
        raise BusanSuyeongContractError("lifelong identity/office mismatch")
    heading = _one(soup.select("h2.enrolTit"), "lifelong detail heading")
    prefix = _one(heading.select(":scope > span"), "lifelong office prefix")
    if _text(prefix) != f"[{BUSAN_LIFELONG_SUYEONG_OFFICE_NAME}]":
        raise BusanSuyeongContractError("lifelong office prefix changed")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")):
        raise BusanSuyeongContractError("lifelong list/detail title mismatch")
    labels, safe, _skipped = _safe_platform_detail_values(soup)
    detail_start, detail_end = _date_pair(safe.get("교육기간"), "lifelong detail education period")
    if (detail_start, detail_end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanSuyeongContractError("lifelong detail education dates mismatch")
    if parent.get("apply_start") and parent.get("apply_end"):
        detail_apply_start, detail_apply_end = _date_pair(
            safe.get("일반모집기간"), "lifelong detail application period"
        )
        if (detail_apply_start, detail_apply_end) != (
            _clean(parent.get("apply_start")),
            _clean(parent.get("apply_end")),
        ):
            raise BusanSuyeongContractError("lifelong detail application dates mismatch")
    controls = soup.select("#learning_aply_btn")
    source_status = _clean(raw.get("source_status"))
    active = source_status in {"접수중", "대기접수"}
    control_label = ""
    application_type = "INFO_ONLY"
    if active:
        control = _one(controls, "lifelong application control")
        control_label = _text(control)
        if (
            control_label not in {"우선모집신청", "일반모집신청", "수강신청", "대기자신청"}
            or _clean(control.get("onclick")) != "fn_learning_apply(); return false;"
        ):
            raise BusanSuyeongContractError("lifelong application control changed")
        application_type = "WAITLIST_APPLY" if control_label == "대기자신청" else "ONLINE_RESERVATION"
    elif controls:
        raise BusanSuyeongContractError("closed lifelong row became actionable")
    result = dict(parent)
    result.update(
        {
            "status": "OPEN" if active else "CLOSED",
            "application_url": (busan_suyeong_lifelong_detail_url(identity) if active else ""),
            "application_type": application_type,
            "reservation_available": active,
            "target": safe.get("교육대상", ""),
            "venue_name": safe.get("교육장소") or BUSAN_LIFELONG_SUYEONG_OFFICE_NAME,
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
    if _text(_one(soup.select("title"), "Busan city list title")) != _CITY_LIST_TITLE:
        raise BusanSuyeongContractError("Busan city list title changed")
    form = _one(soup.select("form#srchForm"), "Busan city search form")
    if _clean(form.get("method")).casefold() != "get" or urlparse(_clean(form.get("action"))).path != "/lctre":
        raise BusanSuyeongContractError("Busan city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "Busan city curPage field")
    if _clean(page_field.get("value")) != str(page):
        raise BusanSuyeongContractError("Busan city form page differs from request")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_SUYEONG_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanSuyeongContractError(f"Busan city {name} filter changed")
    end_link = _one(soup.select("div.paginate > a.pgEnd[href]"), "Busan city last page")
    end_url = urljoin(BUSAN_CITY_SUYEONG_URL, _clean(end_link.get("href")))
    parsed = urlparse(end_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != BUSAN_CITY_LIST_PATH
        or parsed.fragment
        or parsed.params
        or set(query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or _query_one(query, "srchGugun") != BUSAN_CITY_SUYEONG_GUGUN
        or _query_one(query, "srchResveInsttCd") != BUSAN_CITY_RESIDENT_OFFICE
    ):
        raise BusanSuyeongContractError("unsafe Busan city last-page control")
    last_raw = _query_one(query, "curPage")
    if not last_raw.isdigit() or int(last_raw) < 1:
        raise BusanSuyeongContractError("invalid Busan city last page")
    last = int(last_raw)
    if expected_last is not None and last != expected_last:
        raise BusanSuyeongContractError("Busan city displayed last page changed")
    roots = soup.select("ul.reserveList")
    if page <= last:
        root: Optional[Tag] = _one(roots, "Busan city reserve list")
    elif page == last + 1:
        if roots:
            raise BusanSuyeongContractError("Busan city sentinel gained a list")
        root = None
    else:
        raise BusanSuyeongContractError("Busan city request passed sentinel")
    return last, root


def _city_card_ranges(value: Any, label: str) -> tuple[str, str, str, str]:
    match = _CITY_CARD_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanSuyeongContractError(f"{label} changed")
    try:
        apply_start, apply_end, start, end = (date.fromisoformat(part) for part in match.groups())
    except ValueError as exc:
        raise BusanSuyeongContractError(f"{label} contains invalid date") from exc
    if apply_end < apply_start or end < start:
        raise BusanSuyeongContractError(f"{label} is reversed")
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
    last, root = _city_list_contract(soup, page=page, expected_last=expected_last)
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root is not None else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"),
            "Busan city course link",
        )
        action = _clean(link.get("onclick"))
        action_match = _CITY_ACTION_RE.fullmatch(action)
        if not action_match:
            raise BusanSuyeongContractError("Busan city identity action changed")
        group_id, program_id = action_match.groups()
        title_node = _one(link.select(":scope .tit"), "Busan city title")
        visible_title = _text(title_node)
        title = _clean(title_node.get("title"))
        if not title or visible_title not in {title, f"[권역]{title}"}:
            raise BusanSuyeongContractError("Busan city title changed")
        status_node = _one(link.select(":scope .statusMark"), "Busan city status")
        source_status = _text(status_node)
        if source_status not in _CITY_STATUS_MAP:
            raise BusanSuyeongContractError("unknown Busan city status")
        values_root = _one(link.select(":scope .infoBox > dl"), "Busan city values")
        headings = values_root.find_all("dt", recursive=False)
        values = values_root.find_all("dd", recursive=False)
        labels = tuple(_text(heading) for heading in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(headings):
            raise BusanSuyeongContractError("Busan city card labels changed")
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanSuyeongContractError("Busan city safe card value is empty")
        branch = safe["기관"]
        if not branch.startswith("수영구 ") or not branch.endswith("주민자치회"):
            raise BusanSuyeongContractError("Busan city row left Suyeong owner")
        apply_start, apply_end, start, end = _city_card_ranges(
            safe["일자"], f"Busan city page {page} row {position} dates"
        )
        rows.append(
            {
                "provider": BUSAN_SUYEONG_PROVIDER,
                "provider_course_id": (f"{BUSAN_SUYEONG_PROVIDER}:reserve:{group_id}:{program_id}"),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"reserve-{group_id}",
                "preserve_branch": True,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": busan_suyeong_city_detail_url(group_id, program_id),
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
                "municipality_code": BUSAN_SUYEONG_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_SUYEONG_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "수영구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "parser": BUSAN_SUYEONG_PARSER,
                    "source_catalog": "busan_reserve_suyeong_resident_centres",
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
    expected_count = 0 if page == last + 1 else 10 if page < last else len(rows)
    if page < last and len(rows) != expected_count:
        raise BusanSuyeongContractError("Busan city intermediate page is short")
    if page == last and not 1 <= len(rows) <= 10:
        raise BusanSuyeongContractError("Busan city final page row count changed")
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
        raise BusanSuyeongContractError(f"{label} changed")
    try:
        start, end = (date.fromisoformat(part) for part in found)
    except ValueError as exc:
        raise BusanSuyeongContractError(f"{label} has invalid date") from exc
    if end < start:
        raise BusanSuyeongContractError(f"{label} is reversed")
    return start.isoformat(), end.isoformat()


def _city_method_key(value: Any) -> str:
    return "".join(re.sub(r",\s*,", ",", _clean(value)).split())


def _safe_city_detail_values(info: Tag) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(definition.find_all("dt", recursive=False), "Busan city detail label")
        value = _one(definition.find_all("dd", recursive=False), "Busan city detail value")
        label = _text(heading)
        if label in labels:
            raise BusanSuyeongContractError("duplicate Busan city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in _CITY_DETAIL_SKIPPED_LABELS:
            skipped.add(label)
        else:
            raise BusanSuyeongContractError(f"unknown Busan city detail field {label!r}")
    without_attachment = [label for label in labels if label != "첨부파일"]
    if tuple(without_attachment) != _CITY_DETAIL_REQUIRED_LABELS:
        raise BusanSuyeongContractError("Busan city detail field order changed")
    if "문의전화" not in skipped:
        raise BusanSuyeongContractError("Busan city inquiry boundary changed")
    return labels, safe, skipped


def _parse_city_detail(soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    if _compare_url(final_url) != _compare_url(busan_suyeong_city_detail_url(group_id, program_id)):
        raise BusanSuyeongContractError("Busan city detail response scope changed")
    if _text(_one(soup.select("title"), "Busan city detail title")) != _CITY_LIST_TITLE:
        raise BusanSuyeongContractError("Busan city detail title changed")
    form = _one(soup.select("form#viewForm"), "Busan city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanSuyeongContractError("Busan city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSuyeongContractError("Busan city detail identity changed")
    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city detail heading")
    source_status = _text(_one(heading.select(":scope .statusMark"), "city detail status"))
    direct_title = _clean(
        " ".join(_clean(child) for child in heading.children if isinstance(child, NavigableString) and _clean(child))
    )
    parent_title = _clean(parent.get("title"))
    if direct_title not in {parent_title, f"[권역]{parent_title}"} or source_status != _clean(raw.get("source_status")):
        raise BusanSuyeongContractError("Busan city list/detail heading mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "Busan city safe detail values",
    )
    _labels, safe, skipped = _safe_city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanSuyeongContractError("Busan city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanSuyeongContractError("Busan city free-form boundary changed")
    start, end = _city_detail_dates(safe["운영기간"], "city operating period")
    apply_start, apply_end = _city_detail_dates(safe["신청기간"], "city application period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanSuyeongContractError("Busan city list/detail dates mismatch")
    for label, expected in (
        ("신청방법", raw.get("source_application_method")),
        ("운영기관", parent.get("branch")),
        ("대상", parent.get("target")),
    ):
        actual_key = _city_method_key(safe[label]) if label == "신청방법" else _clean(safe[label])
        expected_key = _city_method_key(expected) if label == "신청방법" else _clean(expected)
        if actual_key != expected_key:
            raise BusanSuyeongContractError(f"Busan city {label} mismatch")
    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanSuyeongContractError("multiple Busan city controls")
    control_label = _text(controls[0]) if controls else ""
    normalized_status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    application_type = "INFO_ONLY"
    if normalized_status == "OPEN":
        if "온라인" in method:
            if not controls or not any(token in control_label for token in ("신청", "예약")):
                raise BusanSuyeongContractError("open online city row lacks control")
            active = True
            application_type = "ONLINE_RESERVATION"
        elif any(token in method for token in ("방문", "전화")):
            if control_label not in {"", "방문예약", "전화접수"}:
                raise BusanSuyeongContractError("offline city control changed")
            application_type = "OFFLINE_APPLY"
        else:
            raise BusanSuyeongContractError("unknown Busan city method")
    elif normalized_status == "CLOSED" and control_label not in {"", "접수마감"}:
        raise BusanSuyeongContractError("closed Busan city control changed")
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


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
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


class _Runner:
    def __init__(
        self,
        *,
        timeout: int,
        maximum: int,
        fetcher: Fetcher,
        session_factory: SessionFactory,
        sleeper: Sleeper,
    ) -> None:
        self.timeout = timeout
        self.maximum = maximum
        self.fetcher = fetcher
        self.session_factory = session_factory
        self.sleeper = sleeper
        self.requests = 0
        self.retries = 0
        self.sessions_created = 0
        self._lock = threading.Lock()
        self._local = threading.local()
        self._sessions: list[Any] = []

    def _session(self) -> Any:
        current = getattr(self._local, "session", None)
        if current is None:
            current = self.session_factory()
            self._local.session = current
            with self._lock:
                self.sessions_created += 1
                self._sessions.append(current)
        return current

    def _consume(self, retry: bool) -> None:
        with self._lock:
            if self.requests >= self.maximum:
                raise BusanSuyeongContractError("network request cap reached")
            self.requests += 1
            if retry:
                self.retries += 1

    def get(self, url: str, parser: Parser) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in range(BUSAN_SUYEONG_FETCH_ATTEMPTS):
            self._consume(attempt > 0)
            try:
                response = self.fetcher(self._session(), url, self.timeout)
                if isinstance(response, tuple) and len(response) == 2 and isinstance(response[0], BeautifulSoup):
                    soup, final_url = response
                else:
                    status = int(getattr(response, "status_code", 0) or 0)
                    final_url = _clean(getattr(response, "url", url)) or url
                    headers = getattr(response, "headers", {}) or {}
                    content = bytes(getattr(response, "content", b"") or b"")
                    if status != 200:
                        raise _TransientFetchError(f"HTTP {status}")
                    if len(content) > BUSAN_SUYEONG_MAX_HTML_BYTES:
                        raise BusanSuyeongContractError("HTML response is too large")
                    content_type = _clean(headers.get("content-type")).casefold()
                    if content_type and "html" not in content_type:
                        raise _TransientFetchError("response is not HTML")
                    text = getattr(response, "text", "")
                    if not isinstance(text, str) or not text.strip():
                        raise _TransientFetchError("empty HTML response")
                    soup = BeautifulSoup(text, "html.parser")
                if _compare_url(final_url) != _compare_url(url):
                    raise BusanSuyeongContractError("request response URL changed")
                return parser(soup, final_url)
            except BusanSuyeongContractError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
            if attempt + 1 < BUSAN_SUYEONG_FETCH_ATTEMPTS:
                self.sleeper(0.35 * (attempt + 1))
        raise BusanSuyeongContractError(f"failed source contract after retries: {last_error}") from last_error

    def close(self) -> None:
        for session in self._sessions:
            _close_quietly(session)


def _parallel_fetch(
    runner: _Runner,
    jobs: Sequence[tuple[Any, str, Parser]],
    *,
    max_workers: int,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(runner.get, url, parser): key for key, url, parser in jobs}
        for future in as_completed(futures):
            key = futures[future]
            result[key] = future.result()
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
        "platform_current_external_duplicate_rows": 0,
        "platform_semantic_censuses": 0,
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
        "parser": BUSAN_SUYEONG_PARSER,
        "provider": BUSAN_SUYEONG_PROVIDER,
        "municipality_code": BUSAN_SUYEONG_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_SUYEONG_MUNICIPALITY_NAME,
        "registered_url": BUSAN_SUYEONG_HOME_URL,
        "canonical_url": BUSAN_SUYEONG_CANONICAL_URL,
        "city_canonical_url": BUSAN_CITY_SUYEONG_URL,
        "lifelong_office_code": BUSAN_LIFELONG_SUYEONG_OFFICE,
        "ownership_scope": BUSAN_SUYEONG_OWNERSHIP_SCOPE,
        "candidate_ids": dict(BUSAN_SUYEONG_CANDIDATE_IDS),
        "owner_boundary_audit": dict(BUSAN_SUYEONG_OWNER_BOUNDARY_AUDIT),
        "discovery_audit": dict(BUSAN_SUYEONG_DISCOVERY_AUDIT),
        "pii_fields_never_read": BUSAN_SUYEONG_PII_FIELDS_NEVER_READ,
    }


def _unique_rows(rows: Sequence[Mapping[str, Any]], *, field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if field == "source_data_sid":
            identity = _clean(row.get("raw_fields", {}).get("source_data_sid"))
        elif field == "platform_identity":
            identity = _clean(row.get("raw_fields", {}).get("identity"))
        else:
            identity = _clean(row.get("provider_course_id"))
        if not identity or identity in result:
            raise BusanSuyeongContractError(f"{label} has duplicate identity")
        result[identity] = dict(row)
    return result


def collect_busan_suyeong_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 250,
    detail_limit: int = 200,
    max_requests: int = 450,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_SUYEONG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future snapshot of all Suyeong ledgers."""

    meta = _base_meta()
    if not is_busan_suyeong_education_target(target):
        meta["configured_collection_error"] = "target does not match the exact registered/canonical Suyeong owner"
        return [], BUSAN_SUYEONG_PARSER, meta
    try:
        timeout = _positive_int(timeout, "timeout")
        max_pages = _positive_int(max_pages, "max_pages")
        detail_limit = _positive_int(detail_limit, "detail_limit")
        max_requests = _positive_int(max_requests, "max_requests")
        max_workers = _positive_int(max_workers, "max_workers")
        cutoff = _today(today)
    except Exception as exc:
        meta["configured_collection_error"] = str(exc)
        return [], BUSAN_SUYEONG_PARSER, meta
    runner = _Runner(
        timeout=timeout,
        maximum=max_requests,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _default_session_factory,
        sleeper=sleeper,
    )
    try:
        first_rows, district_total, district_last = runner.get(
            busan_suyeong_list_url(1),
            lambda soup, _final: _parse_local_page(soup, page=1),
        )
        if district_last + 1 > max_pages:
            meta["source_cap_reached"] = True
            raise BusanSuyeongContractError("district pages exceed max_pages")
        district_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
        jobs: list[tuple[int, str, Parser]] = []
        for page in range(2, district_last + 1):
            jobs.append(
                (
                    page,
                    busan_suyeong_list_url(page),
                    lambda soup, _final, page=page: _parse_local_page(
                        soup,
                        page=page,
                        expected_total=district_total,
                        expected_last=district_last,
                    )[0],
                )
            )
        district_pages.update(_parallel_fetch(runner, jobs, max_workers=max_workers))
        sentinel_rows, _, _ = runner.get(
            busan_suyeong_list_url(district_last + 1),
            lambda soup, _final: _parse_local_page(
                soup,
                page=district_last + 1,
                expected_total=district_total,
                expected_last=district_last,
            ),
        )
        if sentinel_rows:
            raise BusanSuyeongContractError("district sentinel is not empty")
        district_rechecks = _parallel_fetch(
            runner,
            (
                (
                    "first",
                    busan_suyeong_list_url(1),
                    lambda soup, _final: _parse_local_page(
                        soup,
                        page=1,
                        expected_total=district_total,
                        expected_last=district_last,
                    )[0],
                ),
                (
                    "last",
                    busan_suyeong_list_url(district_last),
                    lambda soup, _final: _parse_local_page(
                        soup,
                        page=district_last,
                        expected_total=district_total,
                        expected_last=district_last,
                    )[0],
                ),
            ),
            max_workers=min(max_workers, 2),
        )
        if _local_signature(district_rechecks["first"]) != _local_signature(district_pages[1]) or _local_signature(
            district_rechecks["last"]
        ) != _local_signature(district_pages[district_last]):
            raise BusanSuyeongContractError("district boundary changed during crawl")
        district_rows = [row for page in range(1, district_last + 1) for row in district_pages[page]]
        if len(district_rows) != district_total:
            raise BusanSuyeongContractError("district declared/parsed total differs")
        district_by_id = _unique_rows(district_rows, field="source_data_sid", label="district ledger")

        first_platform, platform_last = runner.get(
            busan_suyeong_lifelong_list_url(1),
            lambda soup, _final: _parse_platform_page(soup, page=1),
        )
        platform_sentinel, _ = runner.get(
            busan_suyeong_lifelong_list_url(2),
            lambda soup, _final: _parse_platform_page(soup, page=2, expected_last=platform_last),
        )
        second_platform, _ = runner.get(
            busan_suyeong_lifelong_list_url(1),
            lambda soup, _final: _parse_platform_page(soup, page=1, expected_last=platform_last),
        )
        if platform_sentinel or _platform_archive_signature(first_platform) != _platform_archive_signature(
            second_platform
        ):
            raise BusanSuyeongContractError("lifelong census/sentinel is unstable")
        _unique_rows(first_platform, field="platform_identity", label="lifelong office")
        external_rows: list[dict[str, Any]] = []
        native_source_rows: list[dict[str, Any]] = []
        for row in first_platform:
            kind = _clean(row.get("raw_fields", {}).get("identity_kind"))
            if kind == "external":
                external_rows.append(dict(row))
            elif kind == "internal":
                native_source_rows.append(dict(row))
            else:
                raise BusanSuyeongContractError("unknown lifelong identity kind")
        external_ids: list[str] = []
        for row in external_rows:
            identity = canonical_busan_suyeong_course_identity(row.get("raw_fields", {}).get("identity"))
            owner = district_by_id.get(identity)
            if not identity or owner is None:
                raise BusanSuyeongContractError("lifelong external row has no exact district owner")
            if not _clean(owner.get("title")).endswith(_clean(row.get("title"))):
                raise BusanSuyeongContractError("lifelong external/district title mismatch")
            if (
                _clean(owner.get("start_date")),
                _clean(owner.get("end_date")),
            ) != (
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
            ):
                raise BusanSuyeongContractError("lifelong external/district period mismatch")
            external_ids.append(identity)
        if len(set(external_ids)) != len(external_ids):
            raise BusanSuyeongContractError("duplicate lifelong external dataSid")
        native_rows = [_platform_native_row(row) for row in native_source_rows]

        first_city, city_last = runner.get(
            busan_suyeong_city_list_url(1),
            lambda soup, _final: _parse_city_page(soup, page=1),
        )
        if city_last + 1 > max_pages:
            meta["source_cap_reached"] = True
            raise BusanSuyeongContractError("Busan city pages exceed max_pages")
        city_pages: dict[int, list[dict[str, Any]]] = {1: first_city}
        city_jobs: list[tuple[int, str, Parser]] = []
        for page in range(2, city_last + 1):
            city_jobs.append(
                (
                    page,
                    busan_suyeong_city_list_url(page),
                    lambda soup, _final, page=page: _parse_city_page(soup, page=page, expected_last=city_last)[0],
                )
            )
        city_pages.update(_parallel_fetch(runner, city_jobs, max_workers=max_workers))
        city_sentinel, _ = runner.get(
            busan_suyeong_city_list_url(city_last + 1),
            lambda soup, _final: _parse_city_page(soup, page=city_last + 1, expected_last=city_last),
        )
        if city_sentinel:
            raise BusanSuyeongContractError("Busan city sentinel is not empty")
        city_rechecks = _parallel_fetch(
            runner,
            (
                (
                    "first",
                    busan_suyeong_city_list_url(1),
                    lambda soup, _final: _parse_city_page(soup, page=1, expected_last=city_last)[0],
                ),
                (
                    "last",
                    busan_suyeong_city_list_url(city_last),
                    lambda soup, _final: _parse_city_page(soup, page=city_last, expected_last=city_last)[0],
                ),
            ),
            max_workers=min(max_workers, 2),
        )
        if _city_signature(city_rechecks["first"]) != _city_signature(city_pages[1]) or _city_signature(
            city_rechecks["last"]
        ) != _city_signature(city_pages[city_last]):
            raise BusanSuyeongContractError("Busan city boundary changed during crawl")
        city_rows = [row for page in range(1, city_last + 1) for row in city_pages[page]]
        _unique_rows(city_rows, field="provider_course_id", label="Busan city")

        district_current = [
            row
            for row in district_rows
            if row.get("end_date") and date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        platform_native_current = [
            row for row in native_rows if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        city_current = [row for row in city_rows if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff]
        current_rows = district_current + platform_native_current + city_current
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise BusanSuyeongContractError("current details exceed detail_limit")
        detail_jobs: list[tuple[str, str, Parser]] = []
        for row in current_rows:
            identity = _clean(row.get("provider_course_id"))
            raw = row.get("raw_fields", {})
            catalog = _clean(raw.get("source_catalog"))
            url = _clean(row.get("raw_url"))
            if catalog == "suyeong_complete_district_education":

                def parser(soup: BeautifulSoup, final: str, row: dict[str, Any] = row) -> dict[str, Any]:
                    return _parse_local_detail(soup, final, row)

            elif catalog == "busan_lifelong_suyeong_native":

                def parser(soup: BeautifulSoup, final: str, row: dict[str, Any] = row) -> dict[str, Any]:
                    return _parse_platform_detail(soup, final, row)

            elif catalog == "busan_reserve_suyeong_resident_centres":

                def parser(soup: BeautifulSoup, final: str, row: dict[str, Any] = row) -> dict[str, Any]:
                    return _parse_city_detail(soup, final, row)

            else:
                raise BusanSuyeongContractError("unknown current detail ledger")
            detail_jobs.append((identity, url, parser))
        enriched_map = _parallel_fetch(runner, detail_jobs, max_workers=max_workers)
        enriched = [enriched_map[_clean(row.get("provider_course_id"))] for row in current_rows]
        sanitized: list[dict[str, Any]] = []
        privacy_redactions = 0
        for row in enriched:
            safe, count = _sanitize_row(row)
            sanitized.append(safe)
            privacy_redactions += count
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(sanitized))
        if len(result) != len(sanitized):
            raise BusanSuyeongContractError("dedupe changed atomic row count")
        if len({_clean(row.get("provider_course_id")) for row in result}) != len(result):
            raise BusanSuyeongContractError("returned provider identity is not unique")
        result.sort(key=lambda row: _clean(row.get("provider_course_id")))

        current_external = sum(
            bool(district_by_id[identity].get("end_date"))
            and date.fromisoformat(_clean(district_by_id[identity].get("end_date"))) >= cutoff
            for identity in external_ids
        )
        required_list_requests = district_last + 3 + 3 + city_last + 3
        meta.update(
            {
                "pages": district_last + platform_last + city_last,
                "list_requests": required_list_requests,
                "required_list_requests": required_list_requests,
                "sentinel_requests": 3,
                "stability_rechecks": 5,
                "detail_attempts": len(current_rows),
                "detail_pages": len(enriched),
                "detail_errors": 0,
                "source_total": len(district_rows) + len(first_platform) + len(city_rows),
                "source_rows": len(district_rows) + len(first_platform) + len(city_rows),
                "unique_education_source_rows": len(district_rows) + len(native_rows) + len(city_rows),
                "current_source_count": len(current_rows),
                "expired_count": (len(district_rows) + len(native_rows) + len(city_rows)) - len(current_rows),
                "non_current_count": (len(district_rows) + len(native_rows) + len(city_rows)) - len(current_rows),
                "returned_count": len(result),
                "district_source_rows": len(district_rows),
                "district_data_pages": district_last,
                "district_current_count": len(district_current),
                "district_source_status_counts": dict(
                    Counter(_clean(row.get("raw_fields", {}).get("source_status")) for row in district_rows)
                ),
                "district_current_source_status_counts": dict(
                    Counter(_clean(row.get("raw_fields", {}).get("source_status")) for row in district_current)
                ),
                "platform_source_rows": len(first_platform),
                "platform_native_rows": len(native_rows),
                "platform_native_current_count": len(platform_native_current),
                "platform_external_duplicate_rows": len(external_rows),
                "platform_external_unmatched_rows": 0,
                "platform_current_external_duplicate_rows": current_external,
                "platform_semantic_censuses": 2,
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "city_source_status_counts": dict(
                    Counter(_clean(row.get("raw_fields", {}).get("source_status")) for row in city_rows)
                ),
                "application_control_count": sum(bool(row.get("reservation_available")) for row in result),
                "offline_application_count": sum(
                    _clean(row.get("application_type")) == "OFFLINE_APPLY" for row in result
                ),
                "status_counts": dict(Counter(_clean(row.get("status")) for row in result)),
                "branch_counts": dict(Counter(_clean(row.get("branch")) for row in result)),
                "branch_count": len({_clean(row.get("branch")) for row in result}),
                "privacy_redactions": privacy_redactions,
                "duplicate_source_identity_count": len(external_rows),
                "pagination_detected": True,
                "pagination_complete": True,
                "details_complete": len(enriched) == len(current_rows),
                "snapshot_complete": True,
                "atomic_union_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "all unique real education rows ended before the crawl date" if not result else ""
                ),
                "configured_collection_error": "",
            }
        )
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        meta["source_cap_reached"] = meta["source_cap_reached"] or "cap" in _clean(exc)
        return [], BUSAN_SUYEONG_PARSER, meta
    finally:
        meta["network_requests"] = runner.requests
        meta["network_retry_count"] = runner.retries
        meta["sessions_created"] = runner.sessions_created
        runner.close()
    return result, BUSAN_SUYEONG_PARSER, meta


collect_courses = collect_busan_suyeong_education
collect = collect_busan_suyeong_education


__all__ = [
    "BUSAN_SUYEONG_PROVIDER",
    "BUSAN_SUYEONG_INFORMATION_PROVIDER",
    "BUSAN_CITY_SUYEONG_PROVIDER",
    "BUSAN_LIFELONG_PROVIDER",
    "BUSAN_SUYEONG_MUNICIPALITY_CODE",
    "BUSAN_SUYEONG_MUNICIPALITY_NAME",
    "BUSAN_SUYEONG_HOME_URL",
    "BUSAN_SUYEONG_INFORMATION_URL",
    "BUSAN_SUYEONG_URL",
    "BUSAN_SUYEONG_CANONICAL_URL",
    "BUSAN_CITY_SUYEONG_URL",
    "BUSAN_LIFELONG_SUYEONG_OFFICE",
    "BUSAN_SUYEONG_PARSER",
    "BUSAN_SUYEONG_CANDIDATE_IDS",
    "BUSAN_SUYEONG_OWNER_BOUNDARY_AUDIT",
    "BUSAN_SUYEONG_DISCOVERY_AUDIT",
    "BusanSuyeongContractError",
    "is_busan_suyeong_education_target",
    "is_target",
    "busan_suyeong_list_url",
    "busan_suyeong_detail_url",
    "busan_suyeong_city_list_url",
    "busan_suyeong_city_detail_url",
    "busan_suyeong_lifelong_list_url",
    "busan_suyeong_lifelong_detail_url",
    "canonical_busan_suyeong_course_identity",
    "collect_busan_suyeong_education",
    "collect_courses",
    "collect",
]
