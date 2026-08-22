"""Atomic education collector for Busan Sasang-gu's public ledgers.

The district catalogue is queried with its native ``eduDate`` lower-bound
filter.  That is the complete current/future owner view; every advertised
page, its empty sentinel and stable boundary pages are mandatory.  Current
rows are then verified against their detail pages.

Busan Lifelong Learning office ``OFFICE_00002633`` is a mixed ledger.  Exact
external Sasang ``couIdx`` links are ownership duplicates and are suppressed,
while native ``LEARNING_*`` courses remain owned here.  Busan integrated
reservation is restricted to the exact Sasang resident-council partition
(``srchGugun=9`` and ``srchResveInsttCd=33``).

Only explicit course fields are read from detail pages.  Contact, instructor,
applicant/enrolment, attachment and free-form fields are neither extracted nor
persisted, and application/account pages are never fetched.  Any pagination,
identity, ownership, detail or privacy-contract drift discards the whole
snapshot.
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


BUSAN_SASANG_PROVIDER = "SASANG_RESERVATION"
BUSAN_SASANG_CANDIDATE_ID = "MUNI_IR_C9EAB987846D"
BUSAN_SASANG_MUNICIPALITY_CODE = "2653000000"
BUSAN_SASANG_MUNICIPALITY_NAME = "부산광역시 사상구"

BUSAN_SASANG_HOST = "www.sasang.go.kr"
BUSAN_SASANG_LIST_PATH = "/user/apply/list.sasang"
BUSAN_SASANG_DETAIL_PATH = "/user/apply/view.sasang"
BUSAN_SASANG_APPLY_PATH = "/user/apply/form.sasang"
BUSAN_SASANG_MENU = "DOM_000001003016000000"
BUSAN_SASANG_PLATFORM_MENU = "DOM_000001003003000000"
BUSAN_SASANG_CONTENTS_SID = "1650"
BUSAN_SASANG_CPATH = "/yeyak"
BUSAN_SASANG_CANONICAL_URL = (
    f"https://{BUSAN_SASANG_HOST}{BUSAN_SASANG_LIST_PATH}?"
    + urlencode(
        {
            "menuCd": BUSAN_SASANG_MENU,
            "contentsSid": BUSAN_SASANG_CONTENTS_SID,
            "cpath": BUSAN_SASANG_CPATH,
        }
    )
)
BUSAN_SASANG_URL = BUSAN_SASANG_CANONICAL_URL

BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_SASANG_OFFICE = "OFFICE_00002633"
BUSAN_LIFELONG_SASANG_OFFICE_NAME = "사상구청"
BUSAN_LIFELONG_PAGE_SIZE = 1000

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_SASANG_GUGUN = "9"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_SASANG_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        {
            "curPage": "1",
            "srchGugun": BUSAN_CITY_SASANG_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        }
    )
)

BUSAN_SASANG_FETCH_ATTEMPTS = 3
BUSAN_SASANG_MAX_WORKERS = 10
BUSAN_SASANG_MAX_HTML_BYTES = 10_000_000
BUSAN_SASANG_PARSER = (
    "sasang_edudate_current_complete_pages+empty_sentinel+stable_first_last+"
    "all_current_safe_details+official_operator_omission+official_schedule_fallback+"
    "exact_status_pairs+"
    "lifelong_office00002633_pageunit1000_two_censuses+"
    "external_couidx_owner_duplicate_suppression+native_learning_current_details+"
    "busan_reserve_gugun9_office33_complete_pages+sentinel+stable_boundaries+"
    "current_safe_details+pii_allowlist+atomic_three_ledger_snapshot"
)
BUSAN_SASANG_OWNERSHIP_SCOPE = (
    "sasang_current_future_district_education_native_platform_courses_and_exact_"
    "busan_city_sasang_resident_council_education"
)

BUSAN_SASANG_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_SASANG_PROVIDER: {
        "decision": "retain_complete_current_future_district_owner",
        "candidate_id": BUSAN_SASANG_CANDIDATE_ID,
        "canonical_url": BUSAN_SASANG_CANONICAL_URL,
        "identity_rule": "exact numeric couIdx",
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "suppress_external_couidx_duplicates_keep_native_learning_ids",
        "office_code": BUSAN_LIFELONG_SASANG_OFFICE,
        "identity_rule": "exact Sasang detail owner URL and couIdx",
    },
    "BUSAN_CITY_RESERVATION": {
        "decision": "collect_exact_sasang_resident_council_partition",
        "url": BUSAN_CITY_SASANG_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_SASANG_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "PRIVATE_BOUNDARY": {
        "decision": "never_read_or_persist",
        "reason": (
            "application/account pages, contacts, instructors, applicant values, "
            "attachments and free-form descriptions are outside the allowlist"
        ),
    },
}

BUSAN_SASANG_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_SASANG_CANONICAL_URL,
    "district_current_rows": 35,
    "district_data_pages": 5,
    "district_source_status_counts": {"접수중": 24, "접수대기": 6, "접수마감": 5},
    "lifelong_office": BUSAN_LIFELONG_SASANG_OFFICE,
    "lifelong_rows": 159,
    "lifelong_external_owner_duplicates": 50,
    "lifelong_external_matching_current_district": 1,
    "lifelong_native_rows": 109,
    "lifelong_native_current_rows": 60,
    "resident_rows": 27,
    "resident_data_pages": 3,
    "resident_source_status_counts": {"접수중": 21, "접수마감": 6},
    "source_rows": 221,
    "duplicate_external_rows": 50,
    "unique_publishable_source_rows": 171,
    "atomic_current_rows": 122,
    "atomic_status_counts": {"OPEN": 45, "SCHEDULED": 6, "CLOSED": 71},
    "active_online_application_rows": 24,
    "required_list_requests": 18,
    "required_detail_requests": 122,
    "complete_network_requests": 140,
}


class BusanSasangContractError(ValueError):
    """Raised when an audited Sasang source contract changes."""


class _TransientFetchError(RuntimeError):
    """Retryable transport or status-200 gateway/error-page failure."""


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_COUIDX_RE = re.compile(r"^[1-9]\d{0,8}$")
_LEARNING_ID_RE = re.compile(r"^LEARNING_\d{8}$")
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_LOCAL_ACTION_RE = re.compile(
    r"^url_chk\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([1-9]\d*)['\"]\s*,\s*"
    r"['\"]([1-9]\d*)['\"]\s*\)\s*;?$"
)
_CITY_ACTION_RE = re.compile(
    r"^fn_viewProgrm\(\s*['\"]([1-9]\d*)['\"]\s*,\s*"
    r"['\"]([1-9]\d*)['\"]\s*\)\s*;?\s*return\s+false;?$"
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
    "접수기간",
    "교육기간",
    "시간",
    "장소",
    "모집인원",
    "운영기관",
    "접수방법",
)
_LOCAL_SAFE_LIST_LABELS = frozenset(_LOCAL_LIST_LABELS) - {"모집인원"}
_LOCAL_REQUIRED_SAFE_LIST_LABELS = frozenset(
    {"접수기간", "교육기간", "장소"}
)
_LOCAL_STATUS_MAP = {
    "접수중": "OPEN",
    "접수대기": "SCHEDULED",
    "접수마감": "CLOSED",
    "접수종료": "CLOSED",
}
_LOCAL_STATUS_CLASSES = {
    "접수중": "ing",
    "접수대기": "wait",
    "접수마감": "end",
    "접수종료": "end",
}
_LOCAL_DETAIL_STATUS_BY_LIST = {
    "접수중": "접수중",
    "접수대기": "접수대기",
    "접수마감": "접수마감",
    "접수종료": "교육중",
}
_LOCAL_DETAIL_STATUS_CLASSES = {
    "접수중": "ing",
    "접수대기": "wait",
    "접수마감": "end",
    "교육중": "end",
}
_LOCAL_DETAIL_KNOWN = frozenset(
    {
        "교육구분", "교육시간", "교육대상", "수강료", "인터넷모집",
        "현재신청자수", "대기자모집", "준비물", "교육기관", "접수방법",
        "교육장소", "홈페이지", "문의전화", "접수기간", "강좌기간",
        "강사명", "첨부파일", "운영기간", "상담여부",
    }
)
_LOCAL_DETAIL_REQUIRED_SAFE = frozenset(
    {"교육구분", "교육대상", "수강료", "인터넷모집", "교육장소", "접수기간", "강좌기간"}
)
_LOCAL_DETAIL_SAFE = _LOCAL_DETAIL_REQUIRED_SAFE | {"교육시간", "접수방법"}

_PLATFORM_DETAIL_REQUIRED = (
    "회차명", "강좌분류", "교육대상", "문의전화", "교육장소", "총 교육시간",
    "교육기간", "교육시간", "수강료", "재료비", "접수인원", "우선모집기간",
    "일반모집기간", "모집방법", "신청상태", "교육상태", "강좌소개",
    "강좌소개 첨부파일", "강사", "강의계획서", "결제방법", "주의사항",
    "검색키워드", "강좌제한",
)
_PLATFORM_OPTIONAL_LABELS = frozenset({"수강료 기타", "직장인 여부"})
_PLATFORM_SAFE_LABELS = frozenset(
    {"교육대상", "교육장소", "교육기간", "교육시간", "수강료", "일반모집기간", "모집방법", "신청상태"}
)
_AUDITED_UNLABELED_PLATFORM_DETAIL = {"LEARNING_00087096": 2}
_AUDITED_CITY_TITLE_PREFIX_OMISSIONS = frozenset(
    {"354:22272", "354:22271", "354:22270", "354:22269", "354:22268", "354:22267"}
)
_AUDITED_DUPLICATE_CITY_DETAIL_FORMS = {"354:22272": 2}

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_STATUS_MAP = {"대기중": "SCHEDULED", "접수대기": "SCHEDULED", "접수중": "OPEN", "대기접수": "OPEN", "접수마감": "CLOSED"}
_CITY_DETAIL_REQUIRED = (
    "운영기간", "신청기간", "취소여부", "신청방법", "수강료", "요일 /시간",
    "문의전화", "운영기관", "대상",
)
_CITY_DETAIL_SAFE = frozenset(_CITY_DETAIL_REQUIRED) - {"문의전화"}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True) if node is not None else "")


def _one(values: Iterable[Any], label: str) -> Any:
    items = list(values)
    if len(items) != 1:
        raise BusanSasangContractError(f"expected one {label}, got {len(items)}")
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
        raise BusanSasangContractError(f"invalid {label}")
    return int(text)


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        try:
            result.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise BusanSasangContractError("invalid source date") from exc
    return result


def _date_pair(value: Any, label: str) -> tuple[str, str]:
    values = _dates(value)
    if len(values) != 2 or values[1] < values[0]:
        raise BusanSasangContractError(f"invalid {label}")
    return values[0].isoformat(), values[1].isoformat()


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
        raise BusanSasangContractError("response URL escaped exact source scope")
    return parse_qs(parsed.query, keep_blank_values=True)


def is_busan_sasang_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_SASANG_PROVIDER:
        return False
    try:
        query = _exact_query_url(
            _target_value(target, "url"), BUSAN_SASANG_HOST, BUSAN_SASANG_LIST_PATH
        )
    except BusanSasangContractError:
        return False
    if query != {
        "menuCd": [BUSAN_SASANG_MENU],
        "contentsSid": [BUSAN_SASANG_CONTENTS_SID],
        "cpath": [BUSAN_SASANG_CPATH],
    }:
        return False
    candidate = _clean(_target_value(target, "candidate_id"))
    return not candidate or candidate == BUSAN_SASANG_CANDIDATE_ID


is_target = is_busan_sasang_education_target


def busan_sasang_list_url(page: int = 1, *, cutoff: date | str) -> str:
    page_number = _positive_int(page, "district page")
    cutoff_date = _today(cutoff)
    return f"https://{BUSAN_SASANG_HOST}{BUSAN_SASANG_LIST_PATH}?" + urlencode(
        {
            "menuCd": BUSAN_SASANG_MENU,
            "contentsSid": BUSAN_SASANG_CONTENTS_SID,
            "cpath": BUSAN_SASANG_CPATH,
            "pageIndex": page_number,
            "searchDateType": "eduDate",
            "searchStartDate": cutoff_date.isoformat(),
            "searchEndate": "",
        }
    )


def busan_sasang_detail_url(identity: Any, page: int = 1) -> str:
    token = _clean(identity)
    if not _COUIDX_RE.fullmatch(token):
        raise BusanSasangContractError("invalid Sasang couIdx")
    page_number = _positive_int(page, "district source page")
    return f"https://{BUSAN_SASANG_HOST}{BUSAN_SASANG_DETAIL_PATH}?" + urlencode(
        {"menuCd": BUSAN_SASANG_MENU, "couIdx": token, "pageIndex": page_number}
    )


def busan_sasang_lifelong_list_url(page: int = 1) -> str:
    page_number = _positive_int(page, "platform page")
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(
        {
            "display_type": "2", "pageUnit": BUSAN_LIFELONG_PAGE_SIZE,
            "l_search_ch": "0", "inst_id": BUSAN_LIFELONG_SASANG_OFFICE,
            "pageIndex": page_number,
        }
    )


def busan_sasang_city_list_url(page: int = 1) -> str:
    page_number = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        {
            "curPage": page_number, "srchGugun": BUSAN_CITY_SASANG_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        }
    )


def busan_sasang_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _positive_int(group_id, "city group identity")
    program = _positive_int(program_id, "city program identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        {"resveGroupSn": group, "progrmSn": program}
    )


def _normalize_label(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).rstrip(":：")


def _value_without_label(node: Tag) -> str:
    clone = BeautifulSoup(str(node), "lxml")
    label = clone.select_one("span.name")
    if label is None:
        raise BusanSasangContractError("district field lost label")
    label.extract()
    return _text(clone)


def _local_list_contract(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    cutoff: date,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[int, int, Optional[Tag]]:
    query = _exact_query_url(final_url, BUSAN_SASANG_HOST, BUSAN_SASANG_LIST_PATH)
    if query != {
        "menuCd": [BUSAN_SASANG_MENU], "contentsSid": [BUSAN_SASANG_CONTENTS_SID],
        "cpath": [BUSAN_SASANG_CPATH], "pageIndex": [str(page)],
        "searchDateType": ["eduDate"], "searchStartDate": [cutoff.isoformat()],
        "searchEndate": [""],
    }:
        raise BusanSasangContractError("district current-scope query changed")
    if _text(_one(soup.select("title"), "district title")) != (
        "( 전체 ) 의 목록 | 교육/강좌/공연 | 사상구 통합예약 시스템"
    ):
        raise BusanSasangContractError("district list title changed")
    form = _one(soup.select("form#applyVO[name='applyVO']"), "district search form")
    if _clean(form.get("method")).casefold() != "post" or urlparse(
        urljoin(BUSAN_SASANG_CANONICAL_URL, _clean(form.get("action")))
    ).path != BUSAN_SASANG_LIST_PATH:
        raise BusanSasangContractError("district search form changed")
    for name, expected in (
        ("pageIndex", str(page)), ("menuCd", BUSAN_SASANG_MENU),
        ("searchStartDate", cutoff.isoformat()), ("searchEndate", ""),
    ):
        field = _one(form.select(f"input[name='{name}']"), f"district {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSasangContractError(f"district {name} filter changed")
    selected = form.select("select[name='searchDateType'] > option[selected]")
    if len(selected) != 1 or _clean(selected[0].get("value")) != "eduDate":
        raise BusanSasangContractError("district date filter changed")
    summary = _text(_one(soup.select("p.boardPage"), "district page summary"))
    numbers = re.findall(r"[\d,]+", summary)
    if len(numbers) != 3:
        raise BusanSasangContractError("district pagination summary changed")
    total, current, last = (int(value.replace(",", "")) for value in numbers)
    if current != page or last != max(1, math.ceil(total / 8)):
        raise BusanSasangContractError("district pagination arithmetic changed")
    if expected_total is not None and total != expected_total:
        raise BusanSasangContractError("district total changed during census")
    if expected_last is not None and last != expected_last:
        raise BusanSasangContractError("district final page changed during census")
    roots = soup.select("div.bbs_edu > ul")
    if page <= last:
        return total, last, _one(roots, "district course list")
    if page == last + 1:
        if roots and roots[0].find_all("li", recursive=False):
            raise BusanSasangContractError("district sentinel is not empty")
        return total, last, roots[0] if roots else None
    raise BusanSasangContractError("district request passed sentinel")


def _base_row(identity: str, title: str) -> dict[str, Any]:
    return {
        "provider": BUSAN_SASANG_PROVIDER,
        "provider_course_id": f"{BUSAN_SASANG_PROVIDER}:district:{identity}",
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": BUSAN_SASANG_MUNICIPALITY_NAME,
        "branch_code": BUSAN_SASANG_MUNICIPALITY_CODE,
        "preserve_branch": True,
        "provider_organizer": "사상구청",
        "category": "통합예약 교육",
        "program_type": "교육/강좌",
        "application_url": "",
        "application_type": "INFO_ONLY",
        "reservation_available": False,
        "fee": "",
        "target": "",
        "municipality_code": BUSAN_SASANG_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_SASANG_MUNICIPALITY_NAME,
        "sido": "부산광역시",
        "sigungu": "사상구",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "collection_type": "complete_current_html_pages+detail_allowlist",
    }


def _parse_local_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    cutoff: date,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    total, last, root = _local_list_contract(
        soup, final_url, page=page, cutoff=cutoff,
        expected_total=expected_total, expected_last=expected_last,
    )
    cards = root.find_all("li", recursive=False) if root else []
    expected = 0 if page == last + 1 else min(8, total - (page - 1) * 8)
    if len(cards) != expected:
        raise BusanSasangContractError("district page row count changed")
    rows: list[dict[str, Any]] = []
    for position, card in enumerate(cards, 1):
        title_link = _one(card.select(":scope > dl > dt span.tit > a"), "district course link")
        if _clean(title_link.get("href")) != "#":
            raise BusanSasangContractError("district list link changed")
        action = _LOCAL_ACTION_RE.fullmatch(_clean(title_link.get("onclick")))
        if not action:
            raise BusanSasangContractError("district identity action changed")
        menu, identity, source_page = action.groups()
        if menu != BUSAN_SASANG_MENU or int(source_page) != page:
            raise BusanSasangContractError("district identity scope changed")
        title = _text(title_link)
        if not title:
            raise BusanSasangContractError("empty district title")
        category = _text(_one(card.select(":scope > dl > dt span.divKind"), "district category"))
        target = _text(_one(card.select(":scope > dl > dt span.divPart"), "district target"))
        status_node = _one(card.select(":scope > dl > dt span.stat"), "district status")
        source_status = _text(status_node)
        if (
            source_status not in _LOCAL_STATUS_MAP
            or _LOCAL_STATUS_CLASSES[source_status] not in set(status_node.get("class", []))
        ):
            raise BusanSasangContractError("district status changed")
        fields = card.select(":scope > dl > dd > ul > li")
        labels = tuple(
            _normalize_label(_text(_one(field.select(":scope > span.name"), "district field label")))
            for field in fields
        )
        if labels != _LOCAL_LIST_LABELS:
            raise BusanSasangContractError("district list field order changed")
        safe: dict[str, str] = {}
        for field, label in zip(fields, labels):
            if label in _LOCAL_SAFE_LIST_LABELS:
                safe[label] = _value_without_label(field)
        if any(not safe.get(label) for label in _LOCAL_REQUIRED_SAFE_LIST_LABELS):
            raise BusanSasangContractError("empty district safe list value")
        apply_start, apply_end = _date_pair(safe["접수기간"], "district application period")
        start, end = _date_pair(safe["교육기간"], "district education period")
        if date.fromisoformat(end) < cutoff:
            raise BusanSasangContractError("district date filter returned expired course")
        row = _base_row(identity, title)
        row.update(
            {
                "raw_url": busan_sasang_detail_url(identity, page),
                "status": _LOCAL_STATUS_MAP[source_status],
                "category": category,
                "target": target,
                "period": f"{start} ~ {end}", "start_date": start, "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start, "apply_end": apply_end,
                "schedule_raw": safe["시간"], "venue_name": safe["장소"],
                "application_method_raw": safe["접수방법"],
            }
        )
        row["raw_fields"] = {
            "parser": BUSAN_SASANG_PARSER,
            "source_catalog": "sasang_current_future_district_education",
            "source_identity": identity,
            "source_page": page,
            "source_position": position,
            "source_status": source_status,
            "list_operator_omitted": not bool(safe.get("운영기관")),
            "list_schedule_omitted": not bool(safe.get("시간")),
            "current_scope_cutoff": cutoff.isoformat(),
            "enrollment_value_never_read": True,
            "detail_verified": False,
            "application_form_fetched": False,
            "applicant_list_fetched": False,
            "service_family": "education",
        }
        rows.append(row)
    return rows, total, last


def _signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(
        (
            _clean(row.get("provider_course_id")), _clean(row.get("title")),
            _clean(row.get("start_date")), _clean(row.get("end_date")),
            _clean(row.get("apply_start")), _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _direct_title(node: Tag) -> str:
    return _clean(
        " ".join(
            str(child) for child in node.children if isinstance(child, NavigableString)
        )
    )


def _allowlisted_definitions(
    root: Tag,
    *,
    known: frozenset[str],
    safe_labels: frozenset[str],
    selector: str,
) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for item in root.select(selector):
        label_node = _one(item.select(":scope > span.name"), "district detail label")
        label = _normalize_label(_text(label_node))
        if not label or label not in known or label in labels:
            raise BusanSasangContractError(f"unknown/duplicate district detail field {label!r}")
        labels.append(label)
        if label in safe_labels:
            safe[label] = _value_without_label(item)
        else:
            skipped.add(label)
    return labels, safe, skipped


def _parse_local_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("source_identity"))
    source_page = _positive_int(raw.get("source_page"), "district source page")
    query = _exact_query_url(final_url, BUSAN_SASANG_HOST, BUSAN_SASANG_DETAIL_PATH)
    if query != {
        "menuCd": [BUSAN_SASANG_MENU], "couIdx": [identity],
        "pageIndex": [str(source_page)],
    }:
        raise BusanSasangContractError("district detail response identity changed")
    form = _one(soup.select("form[name='sfrm']"), "district detail form")
    if _clean(form.get("method")).casefold() != "post" or urlparse(
        urljoin(BUSAN_SASANG_CANONICAL_URL, _clean(form.get("action")))
    ).path != BUSAN_SASANG_DETAIL_PATH:
        raise BusanSasangContractError("district detail form changed")
    for name, expected in (
        ("pageIndex", str(source_page)), ("couIdx", identity),
        ("menuCd", BUSAN_SASANG_MENU),
    ):
        field = _one(form.select(f"input[name='{name}']"), f"district detail {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSasangContractError("district detail hidden identity changed")
    root = _one(soup.select("div.edu_vtype"), "district safe detail root")
    heading = _one(root.select(":scope > dl.bbs_infor > dt"), "district detail heading")
    status_node = _one(heading.select(":scope > span.stat"), "district detail status")
    detail_status = _text(status_node)
    list_status = _clean(raw.get("source_status"))
    if (
        _LOCAL_DETAIL_STATUS_BY_LIST.get(list_status) != detail_status
        or _LOCAL_DETAIL_STATUS_CLASSES.get(detail_status)
        not in set(status_node.get("class", []))
    ):
        raise BusanSasangContractError("district list/detail status mismatch")
    if _direct_title(heading) != _clean(parent.get("title")):
        raise BusanSasangContractError("district list/detail title mismatch")
    info = _one(root.select(":scope > dl.bbs_infor > dd > ul.infor"), "district details")
    labels, safe, skipped = _allowlisted_definitions(
        info,
        known=_LOCAL_DETAIL_KNOWN,
        safe_labels=_LOCAL_DETAIL_SAFE,
        selector=":scope > li",
    )
    if not _LOCAL_DETAIL_REQUIRED_SAFE.issubset(safe) or any(
        not safe.get(label) for label in _LOCAL_DETAIL_REQUIRED_SAFE
    ):
        raise BusanSasangContractError("district required safe detail field changed")
    start, end = _date_pair(safe["강좌기간"], "district detail education period")
    apply_start, apply_end = _date_pair(
        safe["접수기간"], "district detail application period"
    )
    if (start, end) != (
        _clean(parent.get("start_date")), _clean(parent.get("end_date"))
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")), _clean(parent.get("apply_end"))
    ):
        raise BusanSasangContractError("district list/detail dates mismatch")
    controls = [
        node for node in soup.select("span.btnBbs > a[href]") if _text(node) == "신청하기"
    ]
    active = list_status == "접수중"
    if active:
        control = _one(controls, "district application control")
        parsed = urlparse(urljoin(BUSAN_SASANG_CANONICAL_URL, _clean(control.get("href"))))
        control_query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != BUSAN_SASANG_HOST
            or parsed.path != BUSAN_SASANG_APPLY_PATH
            or control_query != {"menuCd": [BUSAN_SASANG_MENU], "couIdx": [identity]}
        ):
            raise BusanSasangContractError("district application control escaped identity")
    elif controls:
        raise BusanSasangContractError("inactive district course gained application control")
    result = dict(parent)
    institution = safe.get("교육기관", "")
    schedule = safe.get("교육시간") or _clean(parent.get("schedule_raw"))
    schedule_fallback_used = not bool(schedule)
    if schedule_fallback_used:
        schedule = "시간 별도 안내"
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
            "status": _LOCAL_STATUS_MAP[list_status],
            "category": safe["교육구분"],
            "target": safe["교육대상"],
            "fee": safe["수강료"],
            "capacity": safe["인터넷모집"],
            "branch": institution or _clean(parent.get("branch")),
            "provider_organizer": institution or "사상구청",
            "venue_name": safe["교육장소"],
            "schedule_raw": schedule,
            "application_method_raw": safe.get(
                "접수방법", _clean(parent.get("application_method_raw"))
            ),
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": detail_status,
        "detail_application_control": "신청하기" if active else "",
        "contact_value_never_read": "문의전화" in skipped,
        "instructor_value_never_read": "강사명" in skipped,
        "enrollment_value_never_read": "현재신청자수" in skipped,
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
        "detail_field_labels": labels,
        "detail_institution_omitted": "교육기관" not in labels,
        "institution_fallback_used": not bool(institution),
        "detail_schedule_omitted": not bool(safe.get("교육시간")),
        "schedule_fallback_used": schedule_fallback_used,
    }
    return result


def _platform_office() -> _lifelong.BusanOffice:
    # The local object works before and after the shared registry transfers the
    # office to the dedicated Sasang owner.
    return _lifelong.BusanOffice(
        BUSAN_LIFELONG_SASANG_OFFICE,
        BUSAN_LIFELONG_SASANG_OFFICE_NAME,
        ownership="duplicate_dedicated_sasang_owner",
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
        raise BusanSasangContractError("; ".join(errors))
    last, errors = _lifelong._advertised_last(soup)
    if errors:
        raise BusanSasangContractError("; ".join(errors))
    if expected_last is not None and last != expected_last:
        raise BusanSasangContractError("platform final page changed")
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if errors:
        raise BusanSasangContractError("; ".join(errors))
    if page <= last and not rows:
        raise BusanSasangContractError("platform data page became empty")
    if page == last + 1 and rows:
        raise BusanSasangContractError("platform sentinel returned rows")
    if page > last + 1:
        raise BusanSasangContractError("platform request passed sentinel")
    return rows, last


def _platform_signature(rows: Sequence[Mapping[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")), _clean(row.get("start_date")),
            _clean(row.get("end_date")), _clean(row.get("apply_start")),
            _clean(row.get("apply_end")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )


def _platform_external_couidx(row: Mapping[str, Any]) -> str:
    raw = row.get("raw_fields", {})
    if raw.get("identity_kind") != "external":
        raise BusanSasangContractError("platform duplicate is not external")
    parsed = urlparse(_clean(raw.get("identity")))
    query = _exact_query_url(raw.get("identity"), BUSAN_SASANG_HOST, BUSAN_SASANG_DETAIL_PATH)
    if query != {
        "menuCd": [BUSAN_SASANG_PLATFORM_MENU],
        "couIdx": query.get("couIdx", []),
        "pageIndex": ["1"],
    } or len(query.get("couIdx", [])) != 1 or not _COUIDX_RE.fullmatch(query["couIdx"][0]):
        raise BusanSasangContractError("platform external row escaped canonical Sasang owner")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != BUSAN_SASANG_HOST:
        raise BusanSasangContractError("platform external owner changed")
    return query["couIdx"][0]


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(identity):
        raise BusanSasangContractError("invalid native platform identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_SASANG_PROVIDER,
            "provider_course_id": f"{BUSAN_SASANG_PROVIDER}:lifelong:{identity}",
            "prefer_incoming_provider_course_id": True,
            "branch": BUSAN_LIFELONG_SASANG_OFFICE_NAME,
            "branch_code": "sasang-lifelong-office00002633",
            "preserve_branch": True,
            "provider_organizer": BUSAN_LIFELONG_SASANG_OFFICE_NAME,
            "municipality_code": BUSAN_SASANG_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_SASANG_MUNICIPALITY_NAME,
            "sido": "부산광역시", "sigungu": "사상구",
            "collection_category": "공공예약", "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관", "source_group": "municipal_reservation",
            "service_group": "공공강좌", "service_group_policy": "locked",
            "collection_type": "complete_shared_office_census+native_current_detail",
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_SASANG_PARSER,
        "source_catalog": "busan_lifelong_sasang_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_detail_values(
    soup: BeautifulSoup, identity: str
) -> tuple[dict[str, str], set[str], int]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    unlabeled = 0
    for definition in soup.select("div.form_group dl"):
        headings = definition.find_all("dt", recursive=False)
        values = definition.find_all("dd", recursive=False)
        if not headings:
            unlabeled += 1
            continue
        label = _text(_one(headings, "platform detail label"))
        value = _one(values, "platform detail value")
        if not label:
            unlabeled += 1
            continue
        if label in labels:
            raise BusanSasangContractError("duplicate platform detail field")
        labels.append(label)
        if label in _PLATFORM_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    if unlabeled != _AUDITED_UNLABELED_PLATFORM_DETAIL.get(identity, 0):
        raise BusanSasangContractError("platform unlabeled detail boundary changed")
    required = tuple(label for label in labels if label not in _PLATFORM_OPTIONAL_LABELS)
    if required != _PLATFORM_DETAIL_REQUIRED:
        raise BusanSasangContractError("platform detail fields changed")
    return safe, skipped, unlabeled


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    query = _exact_query_url(final_url, _lifelong.BUSAN_LIFELONG_HOST, BUSAN_LIFELONG_DETAIL_PATH)
    if query != {"lng_id": [identity]}:
        raise BusanSasangContractError("platform detail identity changed")
    for name, expected in (
        ("lng_id", identity), ("inst_id", BUSAN_LIFELONG_SASANG_OFFICE),
    ):
        values = {_clean(node.get("value")) for node in soup.select(f"input[name='{name}']")}
        if values != {expected}:
            raise BusanSasangContractError(f"platform detail {name} changed")
    heading = _one(soup.select("h2.enrolTit"), "platform heading")
    if _text(_one(heading.select(":scope > span"), "platform office prefix")) != (
        f"[{BUSAN_LIFELONG_SASANG_OFFICE_NAME}]"
    ):
        raise BusanSasangContractError("platform detail office changed")
    if _direct_title(heading) != _clean(parent.get("title")):
        raise BusanSasangContractError("platform list/detail title mismatch")
    safe, skipped, unlabeled = _platform_detail_values(soup, identity)
    if any(not safe.get(label) for label in _PLATFORM_SAFE_LABELS):
        raise BusanSasangContractError("empty platform safe detail value")
    start, end = _date_pair(safe["교육기간"], "platform education period")
    if (start, end) != (
        _clean(parent.get("start_date")), _clean(parent.get("end_date"))
    ):
        raise BusanSasangContractError("platform list/detail dates mismatch")
    controls = soup.select("#learning_aply_btn")
    if len(controls) > 1:
        raise BusanSasangContractError("multiple platform controls")
    control_label = _text(controls[0]) if controls else ""
    detail_status = safe["신청상태"]
    active = bool(
        len(controls) == 1
        and "접수중" in detail_status
        and _clean(controls[0].get("onclick")) == "fn_learning_apply(); return false;"
        and control_label in {"일반모집신청", "대기자신청", "우선모집신청"}
    )
    if controls and not active:
        raise BusanSasangContractError("platform control/status changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY" if active and control_label == "대기자신청"
                else "ONLINE_RESERVATION" if active else "INFO_ONLY"
            ),
            "reservation_available": active,
            "status": "OPEN" if active else "SCHEDULED" if "접수대기" in detail_status else "CLOSED",
            "target": safe["교육대상"], "venue_name": safe["교육장소"],
            "fee": safe["수강료"], "schedule_raw": safe["교육시간"],
            "application_method_raw": safe["모집방법"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_source_status": detail_status,
        "detail_application_control": control_label,
        "contact_value_never_read": "문의전화" in skipped,
        "enrollment_value_never_read": "접수인원" in skipped,
        "instructor_value_never_read": "강사" in skipped,
        "attachments_never_read": {"강좌소개 첨부파일", "강의계획서"}.issubset(skipped),
        "free_form_values_never_read": {"강좌소개", "주의사항", "검색키워드", "강좌제한"}.issubset(skipped),
        "unlabeled_free_form_blocks_never_read": unlabeled,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, final_url: str, *, page: int
) -> tuple[int, Optional[Tag]]:
    query = _exact_query_url(final_url, BUSAN_CITY_HOST, BUSAN_CITY_LIST_PATH)
    if query != {
        "curPage": [str(page)], "srchGugun": [BUSAN_CITY_SASANG_GUGUN],
        "srchResveInsttCd": [BUSAN_CITY_RESIDENT_OFFICE],
    }:
        raise BusanSasangContractError("city list response query changed")
    if _text(_one(soup.select("title"), "city list title")) != _CITY_LIST_TITLE:
        raise BusanSasangContractError("city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanSasangContractError("city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "city page field")
    if _clean(page_field.get("value")) != str(page):
        raise BusanSasangContractError("city form page changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_SASANG_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanSasangContractError(f"city {name} filter changed")
    end = _one(soup.select("div.paginate > a.pgEnd[href]"), "city final page")
    parsed = urlparse(urljoin(BUSAN_CITY_SASANG_URL, _clean(end.get("href"))))
    end_query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        (parsed.hostname or "").lower() != BUSAN_CITY_HOST
        or parsed.path != BUSAN_CITY_LIST_PATH
        or set(end_query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or end_query.get("srchGugun") != [BUSAN_CITY_SASANG_GUGUN]
        or end_query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
        or len(end_query.get("curPage", [])) != 1
        or not end_query["curPage"][0].isdigit()
    ):
        raise BusanSasangContractError("unsafe city final-page control")
    last = int(end_query["curPage"][0])
    roots = soup.select("ul.reserveList")
    if page <= last:
        return last, _one(roots, "city reserve list")
    if page == last + 1:
        if roots:
            raise BusanSasangContractError("city sentinel is not empty")
        return last, None
    raise BusanSasangContractError("city request passed sentinel")


def _city_date_ranges(value: Any) -> tuple[str, str, str, str]:
    match = _CITY_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanSasangContractError("city card dates changed")
    values = [date.fromisoformat(item).isoformat() for item in match.groups()]
    if values[1] < values[0] or values[3] < values[2]:
        raise BusanSasangContractError("city card date range reversed")
    return values[0], values[1], values[2], values[3]


def _normalize_method(value: Any) -> str:
    return ", ".join(
        part for part in (_clean(part) for part in _clean(value).split(",")) if part
    )


def _parse_city_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(soup, final_url, page=page)
    if expected_last is not None and last != expected_last:
        raise BusanSasangContractError("city final page changed")
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(root.find_all("li", recursive=False) if root else [], 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "city course link")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanSasangContractError("city identity action changed")
        group_id, program_id = action.groups()
        identity = f"{group_id}:{program_id}"
        title_node = _one(link.select(":scope .tit"), "city title")
        title = _text(title_node)
        title_attribute = _clean(title_node.get("title"))
        if not title or (
            title_attribute != title
            and not (
                identity in _AUDITED_CITY_TITLE_PREFIX_OMISSIONS
                and title.startswith("[권역]")
                and title_attribute == title.removeprefix("[권역]")
            )
        ):
            raise BusanSasangContractError("city title attribute changed")
        source_status = _text(_one(link.select(":scope .statusMark"), "city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanSasangContractError("unknown city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanSasangContractError("city card labels changed")
        # Deliberately do not read the final inquiry value.
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanSasangContractError("empty city safe card value")
        branch = safe["기관"]
        if not branch.startswith("사상구 ") or not branch.endswith(" 주민자치회"):
            raise BusanSasangContractError("city course left Sasang owner")
        apply_start, apply_end, start, end = _city_date_ranges(safe["일자"])
        method = _normalize_method(safe["방법"])
        if not method:
            raise BusanSasangContractError("empty city application method")
        row = _base_row(identity, title)
        row.update(
            {
                "provider_course_id": f"{BUSAN_SASANG_PROVIDER}:reserve:{identity}",
                "branch": branch, "branch_code": f"sasang-reserve-{group_id}",
                "provider_organizer": branch,
                "category": "주민자치프로그램",
                "raw_url": busan_sasang_city_detail_url(group_id, program_id),
                "application_method_raw": method,
                "status": _CITY_STATUS_MAP[source_status],
                "period": f"{start} ~ {end}", "start_date": start, "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start, "apply_end": apply_end,
                "schedule_raw": "", "target": safe["대상"],
                "venue_name": safe["장소"],
                "collection_type": "complete_city_partition+current_detail_allowlist",
            }
        )
        row["raw_fields"] = {
            "parser": BUSAN_SASANG_PARSER,
            "source_catalog": "busan_reserve_sasang_resident_councils",
            "source_identity": identity,
            "source_group_id": group_id,
            "source_program_id": program_id,
            "source_page": page,
            "source_position": position,
            "source_status": source_status,
            "source_application_method": method,
            "source_card_dates": safe["일자"],
            "audited_title_attribute_prefix_omission": (
                identity in _AUDITED_CITY_TITLE_PREFIX_OMISSIONS
            ),
            "inquiry_value_never_read": True,
            "detail_verified": False,
            "application_form_fetched": False,
            "applicant_list_fetched": False,
            "service_family": "education",
        }
        rows.append(row)
    return rows, last


def _city_detail_values(info: Tag) -> tuple[dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        label = _text(_one(definition.find_all("dt", recursive=False), "city detail label"))
        value = _one(definition.find_all("dd", recursive=False), "city detail value")
        if label in labels:
            raise BusanSasangContractError("duplicate city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE:
            safe[label] = _text(value)
        elif label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        else:
            raise BusanSasangContractError(f"unknown city detail field {label!r}")
    without_attachment = tuple(label for label in labels if label != "첨부파일")
    if without_attachment != _CITY_DETAIL_REQUIRED or "문의전화" not in skipped:
        raise BusanSasangContractError("city detail field order changed")
    return safe, skipped


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    query = _exact_query_url(final_url, BUSAN_CITY_HOST, BUSAN_CITY_DETAIL_PATH)
    if query != {"resveGroupSn": [group_id], "progrmSn": [program_id]}:
        raise BusanSasangContractError("city detail response identity changed")
    if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
        raise BusanSasangContractError("city detail title changed")
    forms = soup.select("form#viewForm")
    expected_forms = _AUDITED_DUPLICATE_CITY_DETAIL_FORMS.get(
        f"{group_id}:{program_id}", 1
    )
    if len(forms) != expected_forms:
        raise BusanSasangContractError(
            f"expected {expected_forms} city detail form(s), got {len(forms)}"
        )
    form = forms[0]
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanSasangContractError("city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSasangContractError("city detail hidden identity changed")
    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city heading")
    source_status = _text(_one(heading.select(":scope .statusMark"), "city status"))
    if _direct_title(heading) != _clean(parent.get("title")):
        raise BusanSasangContractError("city list/detail title mismatch")
    list_status = _clean(raw.get("source_status"))
    expected_detail_status = "대기자접수" if list_status == "대기접수" else list_status
    if source_status != expected_detail_status:
        raise BusanSasangContractError("city list/detail status mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "city safe detail values",
    )
    safe, skipped = _city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE):
        raise BusanSasangContractError("empty city safe detail value")
    start, end = _date_pair(safe["운영기간"], "city operating period")
    apply_start, apply_end = _date_pair(safe["신청기간"], "city application period")
    if (start, end) != (
        _clean(parent.get("start_date")), _clean(parent.get("end_date"))
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")), _clean(parent.get("apply_end"))
    ):
        raise BusanSasangContractError("city list/detail dates mismatch")
    method = _normalize_method(safe["신청방법"])
    if (
        method != _clean(raw.get("source_application_method"))
        or safe["운영기관"] != _clean(parent.get("branch"))
        or safe["대상"] != _clean(parent.get("target"))
    ):
        raise BusanSasangContractError("city list/detail safe values mismatch")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanSasangContractError("city free-form boundary changed")
    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanSasangContractError("multiple city application controls")
    label = _text(controls[0]) if controls else ""
    status = _CITY_STATUS_MAP[list_status]
    online = "온라인" in method
    active = status == "OPEN" and online
    if active and (len(controls) != 1 or label not in {"예약하기", "대기예약"}):
        raise BusanSasangContractError("online city course lacks exact control")
    if status == "OPEN" and not online and label not in {"", "방문예약"}:
        raise BusanSasangContractError("offline city control changed")
    if status == "CLOSED" and label not in {"", "접수마감"}:
        raise BusanSasangContractError("closed city control changed")
    if status == "SCHEDULED" and label not in {"", "대기중", "접수대기"}:
        raise BusanSasangContractError("scheduled city control changed")
    # One audited legacy record renders the same safe form twice.  Both copies
    # must retain identical identity/course fields; free-form bodies remain
    # outside the allowlist and are not compared or read.
    for duplicate in forms[1:]:
        for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
            field = _one(
                duplicate.select(f":scope > input[name='{name}']"),
                f"duplicate city {name}",
            )
            if _clean(field.get("value")) != expected:
                raise BusanSasangContractError("duplicate city detail identity changed")
        duplicate_heading = _one(
            duplicate.select(":scope > div.contHeader > h3.titPage"),
            "duplicate city heading",
        )
        duplicate_status = _text(
            _one(duplicate_heading.select(":scope .statusMark"), "duplicate city status")
        )
        duplicate_info = _one(
            duplicate.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
            "duplicate city safe values",
        )
        duplicate_safe, duplicate_skipped = _city_detail_values(duplicate_info)
        duplicate_controls = duplicate.select(
            ":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL"
        )
        duplicate_label = _text(duplicate_controls[0]) if duplicate_controls else ""
        if (
            len(duplicate_controls) > 1
            or _direct_title(duplicate_heading) != _clean(parent.get("title"))
            or duplicate_status != source_status
            or duplicate_safe != safe
            or duplicate_skipped != skipped
            or duplicate_label != label
            or len(duplicate.select(":scope > div.reserveDetail")) != 1
        ):
            raise BusanSasangContractError("duplicate city detail forms changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": (
                "WAITLIST_APPLY" if active and list_status == "대기접수"
                else "ONLINE_RESERVATION" if active
                else "OFFLINE_APPLY" if status == "OPEN" else "INFO_ONLY"
            ),
            "reservation_available": active,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
            "application_method_raw": method,
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
        "audited_duplicate_detail_forms": len(forms),
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MoonCen-Sasang-Audit/1.0)",
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
                raise BusanSasangContractError("max_requests cap exhausted")
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise _TransientFetchError(f"HTTP {status}")
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)):
        content = _clean(getattr(response, "text", "")).encode("utf-8")
    if not content or len(content) > BUSAN_SASANG_MAX_HTML_BYTES:
        raise _TransientFetchError("empty or oversized HTML")
    soup = BeautifulSoup(bytes(content), "lxml")
    plain = _clean(soup.get_text(" ", strip=True)).casefold()
    if len(content) < 2048 and any(
        token in plain
        for token in (
            "bad request", "service unavailable", "temporarily unavailable",
            "internal server error",
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
    for attempt in range(1, BUSAN_SASANG_FETCH_ATTEMPTS + 1):
        session = None
        try:
            budget.reserve()
            session = session_factory()
            sessions += 1
            response = fetcher(session, url, timeout)
            soup, final_url = _response_soup(response, url)
            return _FetchResult(parser(soup, final_url), attempt - 1, sessions)
        except BusanSasangContractError:
            raise
        except Exception as exc:
            messages.append(f"attempt {attempt}: {type(exc).__name__}: {_clean(exc)}")
            if attempt < BUSAN_SASANG_FETCH_ATTEMPTS:
                sleeper(min(0.25 * attempt, 0.75))
        finally:
            if session is not None:
                _close_quietly(session)
    raise BusanSasangContractError("; ".join(messages))


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
                _fetch_parsed, url, parser,
                fetcher=fetcher, session_factory=session_factory,
                timeout=timeout, sleeper=sleeper, budget=budget,
            ): key
            for key, url, parser in items
        }
        for future in as_completed(future_map):
            result = future.result()
            key = future_map[future]
            if key in values:
                raise BusanSasangContractError("duplicate fetch key")
            values[key] = result.value
            retries += result.retries
            sessions += result.sessions
    if len(values) != len(items):
        raise BusanSasangContractError("incomplete concurrent fetch")
    return values, retries, sessions


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    return any(
        token in lowered
        for token in (
            "phone", "telephone", "email", "instructor", "teacher", "강사",
            "전화", "메일", "applicant", "contact",
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
        raise BusanSasangContractError("row sanitizer changed type")
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
        "provider": BUSAN_SASANG_PROVIDER,
        "candidate_id": BUSAN_SASANG_CANDIDATE_ID,
        "canonical_url": BUSAN_SASANG_CANONICAL_URL,
        "municipality_code": BUSAN_SASANG_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_SASANG_MUNICIPALITY_NAME,
        "ownership_scope": BUSAN_SASANG_OWNERSHIP_SCOPE,
        "discovery_audit": dict(BUSAN_SASANG_DISCOVERY_AUDIT),
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


def collect_busan_sasang_education(
    target: Any,
    timeout: int = 35,
    max_pages: int = 20,
    detail_limit: int = 180,
    max_requests: int = 220,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_SASANG_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future Sasang education snapshot."""

    meta = _base_meta()
    if not is_busan_sasang_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Sasang education owner"
        )
        return [], BUSAN_SASANG_PARSER, meta
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
        workers = min(max(1, int(max_workers)), BUSAN_SASANG_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_SASANG_PARSER, meta
    if page_cap < 1 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps cannot inspect all three ledgers"
        return [], BUSAN_SASANG_PARSER, meta

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
        url: str, parser: Callable[[BeautifulSoup, str], Any], *, list_phase: bool
    ) -> Any:
        return account(
            _fetch_parsed(
                url, parser, fetcher=fetch, session_factory=factory,
                timeout=request_timeout, sleeper=sleeper, budget=budget,
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
            items, fetcher=fetch, session_factory=factory,
            timeout=request_timeout, sleeper=sleeper, budget=budget,
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
        # Complete native current/future district view.
        first_rows, local_total, local_last = fetch_one(
            busan_sasang_list_url(1, cutoff=cutoff),
            lambda soup, final: _parse_local_page(
                soup, final, page=1, cutoff=cutoff
            ),
            list_phase=True,
        )
        if local_last > page_cap:
            raise BusanSasangContractError(
                f"max_pages cap allows {page_cap} of {local_last} district pages"
            )
        local_pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
        local_pages.update(
            fetch_batch(
                [
                    (
                        page,
                        busan_sasang_list_url(page, cutoff=cutoff),
                        lambda soup, final, p=page: _parse_local_page(
                            soup, final, page=p, cutoff=cutoff,
                            expected_total=local_total, expected_last=local_last,
                        )[0],
                    )
                    for page in range(2, local_last + 1)
                ],
                list_phase=True,
            )
        )
        local_empty, _, _ = fetch_one(
            busan_sasang_list_url(local_last + 1, cutoff=cutoff),
            lambda soup, final: _parse_local_page(
                soup, final, page=local_last + 1, cutoff=cutoff,
                expected_total=local_total, expected_last=local_last,
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if local_empty:
            raise BusanSasangContractError("district sentinel returned rows")
        local_boundaries = sorted({1, local_last})
        local_rechecked = fetch_batch(
            [
                (
                    page,
                    busan_sasang_list_url(page, cutoff=cutoff),
                    lambda soup, final, p=page: _parse_local_page(
                        soup, final, page=p, cutoff=cutoff,
                        expected_total=local_total, expected_last=local_last,
                    )[0],
                )
                for page in local_boundaries
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(local_boundaries)
        for page in local_boundaries:
            if _signature(local_pages[page]) != _signature(local_rechecked[page]):
                raise BusanSasangContractError("district boundary page changed")
        local_rows = [
            row for page in range(1, local_last + 1) for row in local_pages[page]
        ]
        if len(local_rows) != local_total:
            raise BusanSasangContractError("district complete count changed")
        local_by_id: dict[str, dict[str, Any]] = {}
        for row in local_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            if identity in local_by_id:
                raise BusanSasangContractError("duplicate district couIdx")
            local_by_id[identity] = row

        # Two complete platform censuses with a sentinel each.
        platform_censuses: list[list[dict[str, Any]]] = []
        platform_last = 0
        for census_index in range(2):
            rows, current_last = fetch_one(
                busan_sasang_lifelong_list_url(1),
                lambda soup, final: _parse_platform_page(soup, final, page=1),
                list_phase=True,
            )
            if current_last > page_cap:
                raise BusanSasangContractError(
                    f"max_pages cap allows {page_cap} of {current_last} platform pages"
                )
            if current_last != 1:
                raise BusanSasangContractError(
                    "pageUnit1000 no longer contains complete platform census"
                )
            empty, sentinel_last = fetch_one(
                busan_sasang_lifelong_list_url(2),
                lambda soup, final: _parse_platform_page(
                    soup, final, page=2, expected_last=current_last
                ),
                list_phase=True,
            )
            meta["sentinel_requests"] += 1
            if empty or sentinel_last != current_last:
                raise BusanSasangContractError("platform sentinel changed")
            if census_index:
                meta["stability_rechecks"] += 2
            platform_censuses.append(rows)
            platform_last = current_last
        if _platform_signature(platform_censuses[0]) != _platform_signature(
            platform_censuses[1]
        ):
            raise BusanSasangContractError("platform complete censuses changed")
        platform_rows = platform_censuses[0]
        sequences = sorted(
            int(row.get("raw_fields", {}).get("list_sequence") or 0)
            for row in platform_rows
        )
        if sequences != list(range(1, len(platform_rows) + 1)):
            raise BusanSasangContractError("platform sequence changed")
        external_rows = [
            row for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "external"
        ]
        native_source = [
            row for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "internal"
        ]
        if len(external_rows) + len(native_source) != len(platform_rows):
            raise BusanSasangContractError("unexpected platform identity family")
        external_ids = [_platform_external_couidx(row) for row in external_rows]
        if len(external_ids) != len(set(external_ids)):
            raise BusanSasangContractError("repeated platform external couIdx")
        for identity, platform_row in zip(external_ids, external_rows):
            district = local_by_id.get(identity)
            if district is None:
                continue
            if (
                _clean(platform_row.get("title")) != _clean(district.get("title"))
                or (_clean(platform_row.get("start_date")), _clean(platform_row.get("end_date")))
                != (_clean(district.get("start_date")), _clean(district.get("end_date")))
            ):
                raise BusanSasangContractError("platform/current district duplicate changed")
        native_rows = [_platform_native_row(row) for row in native_source]

        # Exact Sasang resident-council partition.
        city_first, city_last = fetch_one(
            busan_sasang_city_list_url(1),
            lambda soup, final: _parse_city_page(soup, final, page=1),
            list_phase=True,
        )
        if city_last > page_cap:
            raise BusanSasangContractError(
                f"max_pages cap allows {page_cap} of {city_last} city pages"
            )
        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first}
        city_pages.update(
            fetch_batch(
                [
                    (
                        page,
                        busan_sasang_city_list_url(page),
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
            busan_sasang_city_list_url(city_last + 1),
            lambda soup, final: _parse_city_page(
                soup, final, page=city_last + 1, expected_last=city_last
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if city_empty:
            raise BusanSasangContractError("city sentinel returned rows")
        city_boundaries = sorted({1, city_last})
        city_rechecked = fetch_batch(
            [
                (
                    page,
                    busan_sasang_city_list_url(page),
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
            if _signature(city_pages[page]) != _signature(city_rechecked[page]):
                raise BusanSasangContractError("city boundary page changed")
        city_rows = [
            row for page in range(1, city_last + 1) for row in city_pages[page]
        ]
        city_ids = [_clean(row.get("provider_course_id")) for row in city_rows]
        if len(city_ids) != len(set(city_ids)):
            raise BusanSasangContractError("duplicate city identity")

        native_current = [
            row for row in native_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        city_current = [
            row for row in city_rows if date.fromisoformat(row["end_date"]) >= cutoff
        ]
        current = local_rows + native_current + city_current
        if len(current) > detail_cap:
            raise BusanSasangContractError(
                f"detail_limit cap allows {detail_cap} of {len(current)} current details"
            )
        detail_items: list[
            tuple[str, str, Callable[[BeautifulSoup, str], dict[str, Any]]]
        ] = []
        for row in local_rows:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]), _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_local_detail(soup, final, parent),
                )
            )
        for row in native_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]), _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_platform_detail(soup, final, parent),
                )
            )
        for row in city_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]), _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_city_detail(soup, final, parent),
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
            raise BusanSasangContractError("dedupe changed complete identity set")
        if len(after_ids) != len(set(after_ids)):
            raise BusanSasangContractError("duplicate identity remained")

        meta.update(
            {
                "district_source_rows": len(local_rows),
                "district_data_pages": local_last,
                "district_current_count": len(local_rows),
                "district_source_status_counts": dict(
                    Counter(row.get("raw_fields", {}).get("source_status") for row in local_rows)
                ),
                "platform_source_rows": len(platform_rows),
                "platform_data_pages": platform_last,
                "platform_external_duplicate_rows": len(external_rows),
                "platform_external_unique_couidx": len(set(external_ids)),
                "platform_external_matching_current_district": sum(
                    identity in local_by_id for identity in external_ids
                ),
                "platform_external_expired_owner_links": sum(
                    identity not in local_by_id for identity in external_ids
                ),
                "platform_native_rows": len(native_rows),
                "platform_native_current_count": len(native_current),
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "city_expired_count": len(city_rows) - len(city_current),
                "city_source_status_counts": dict(
                    Counter(row.get("raw_fields", {}).get("source_status") for row in city_rows)
                ),
                "source_total": len(local_rows) + len(platform_rows) + len(city_rows),
                "duplicate_source_rows": len(external_rows),
                "unique_education_source_rows": len(local_rows) + len(native_rows) + len(city_rows),
                "current_source_count": len(current),
                "expired_count": len(native_rows) - len(native_current) + len(city_rows) - len(city_current),
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
                    "all unique education rows ended before the crawl date" if not result else ""
                ),
                "configured_collection_error": "",
            }
        )
        return result, BUSAN_SASANG_PARSER, meta
    except Exception as exc:
        meta["network_requests"] = budget.count
        message = _clean(exc)
        if "cap" in message:
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = message or exc.__class__.__name__
        return [], BUSAN_SASANG_PARSER, meta


collect_courses = collect_busan_sasang_education


__all__ = [
    "BUSAN_SASANG_PROVIDER",
    "BUSAN_SASANG_CANDIDATE_ID",
    "BUSAN_SASANG_MUNICIPALITY_CODE",
    "BUSAN_SASANG_MUNICIPALITY_NAME",
    "BUSAN_SASANG_CANONICAL_URL",
    "BUSAN_SASANG_URL",
    "BUSAN_LIFELONG_SASANG_OFFICE",
    "BUSAN_CITY_SASANG_URL",
    "BUSAN_SASANG_PARSER",
    "BUSAN_SASANG_OWNERSHIP_SCOPE",
    "BUSAN_SASANG_OWNER_BOUNDARY_AUDIT",
    "BUSAN_SASANG_DISCOVERY_AUDIT",
    "BusanSasangContractError",
    "busan_sasang_list_url",
    "busan_sasang_detail_url",
    "busan_sasang_lifelong_list_url",
    "busan_sasang_city_list_url",
    "busan_sasang_city_detail_url",
    "is_busan_sasang_education_target",
    "is_target",
    "collect_busan_sasang_education",
    "collect_courses",
]
