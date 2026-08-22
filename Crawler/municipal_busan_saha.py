"""Atomic education collector for Busan Saha-gu's public course ledgers.

The owner is the union of the complete Saha lifelong-learning archive, the
two Saha offices on the Busan Lifelong Learning Platform, and the exact
Saha resident-council partition of Busan's integrated reservation service.
Platform external rows are accepted only when their Saha ``seq`` identity
and immutable list fields match the district archive; those rows are then
suppressed as republications.  Native platform rows remain owned here.

Every data page, an immediate empty sentinel, and stable boundary pages (or
two complete one-page platform censuses) are required.  Every current/future
row must also pass an identity-bound, allow-listed detail contract.  A single
network, schema, identity, ownership, cap, or privacy failure discards the
whole snapshot.  Applicant lists/forms, account pages, contact values,
instructor names, attachments, and free-form descriptions are never fetched
or persisted.
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


BUSAN_SAHA_PROVIDER = "MUNI_WWW_SAHA_GO_KR_ED7CDFC9"
BUSAN_SAHA_CANDIDATE_ID = "MUNI_IR_5D221979EFA0"
BUSAN_SAHA_MUNICIPALITY_CODE = "2638000000"
BUSAN_SAHA_MUNICIPALITY_NAME = "부산광역시 사하구"

BUSAN_SAHA_HOST = "www.saha.go.kr"
BUSAN_SAHA_PATH = "/edu/lecture/list.do"
BUSAN_SAHA_DETAIL_PATH = "/edu/lecture/view.do"
BUSAN_SAHA_MID = "0201010000"
BUSAN_SAHA_URL = (
    f"https://{BUSAN_SAHA_HOST}{BUSAN_SAHA_PATH}?"
    + urlencode({"mId": BUSAN_SAHA_MID})
)
BUSAN_SAHA_CANONICAL_URL = BUSAN_SAHA_URL
BUSAN_SAHA_REGISTERED_URL = "https://www.saha.go.kr/edu/main.do"
BUSAN_SAHA_RESERVATION_ALIAS_URL = "https://www.saha.go.kr/reserve/main.do"

BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH
BUSAN_LIFELONG_PAGE_SIZE = 1000
BUSAN_LIFELONG_SAHA_OFFICES = (
    ("OFFICE_00002632", "사하구청"),
    ("OFFICE_00002790", "하단2동 행정복지센터"),
)

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_SAHA_GUGUN = "10"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_SAHA_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_SAHA_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)

BUSAN_SAHA_FETCH_ATTEMPTS = 3
BUSAN_SAHA_MAX_WORKERS = 12
BUSAN_SAHA_MAX_HTML_BYTES = 8_000_000
BUSAN_SAHA_PARSER = (
    "saha_lifelong_seq_all95+empty96_sentinel+stable_first_last+"
    "busan_lifelong_office00002632_and00002790_pageunit1000_two_censuses+"
    "external_seq_identity_duplicate_suppression+"
    "busan_reserve_gugun10_office33_exact_partition+sentinel+stability+"
    "all_current_safe_details+pii_never_read+atomic_three_ledger_snapshot"
)
BUSAN_SAHA_OWNERSHIP_SCOPE = (
    "saha_complete_district_lifelong_archive_native_platform_courses_and_"
    "exact_busan_city_saha_resident_council_education"
)

BUSAN_SAHA_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    BUSAN_SAHA_PROVIDER: {
        "decision": "canonical_complete_saha_education_owner",
        "candidate_id": BUSAN_SAHA_CANDIDATE_ID,
        "url": BUSAN_SAHA_URL,
    },
    BUSAN_LIFELONG_PROVIDER: {
        "decision": "collect_native_and_suppress_exact_external_seq_duplicates",
        "office_codes": tuple(code for code, _name in BUSAN_LIFELONG_SAHA_OFFICES),
    },
    "BUSAN_RESIDENT_COUNCILS": {
        "decision": "collect_exact_saha_partition",
        "url": BUSAN_CITY_SAHA_URL,
        "filter": {
            "srchGugun": BUSAN_CITY_SAHA_GUGUN,
            "srchResveInsttCd": BUSAN_CITY_RESIDENT_OFFICE,
        },
    },
    "LANDING_ALIASES": {
        "decision": "exclude_non_ledger_landing_pages",
        "urls": (BUSAN_SAHA_REGISTERED_URL, BUSAN_SAHA_RESERVATION_ALIAS_URL),
    },
    "APPLICANT_AND_ACCOUNT_BOUNDARY": {
        "decision": "never_fetch_or_persist",
        "reason": "forms, applicant lists, logins, contacts and instructor values contain PII",
    },
}

BUSAN_SAHA_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "canonical_url": BUSAN_SAHA_URL,
    "local_rows": 950,
    "local_data_pages": 95,
    "local_sentinel_page": 96,
    "local_current_rows": 0,
    "local_source_status_counts": {"종강": 583, "접수마감": 367},
    "platform_rows_by_office": {
        "OFFICE_00002632": 73,
        "OFFICE_00002790": 0,
    },
    "platform_external_duplicate_rows": 10,
    "platform_native_rows": 63,
    "platform_native_current_rows": 15,
    "resident_rows": 0,
    "source_rows": 1023,
    "duplicate_external_rows": 10,
    "unique_education_source_rows": 1013,
    "atomic_current_rows": 15,
    "atomic_status_counts": {"OPEN": 3, "CLOSED": 12},
    "active_online_application_rows": 3,
    "required_list_requests": 109,
    "required_detail_requests": 15,
    "complete_network_requests": 124,
}


class BusanSahaContractError(ValueError):
    pass


class _TransientFetchError(RuntimeError):
    pass


SessionFactory = Callable[[], Any]
Fetcher = Callable[[Any, str, int], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_LOCAL_ACTION_RE = re.compile(
    r"fn_view_page\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*\)"
)
_LOCAL_SUMMARY_RE = re.compile(
    r"페이지\s*:\s*(\d+)\s*/\s*(\d+)\s*전체게시물\s*:\s*([\d,]+)"
)
_LOCAL_END_RE = re.compile(r"goPage\(\s*(\d+)\s*\)\s*;?\s*return false;?")
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_SEQ_RE = re.compile(r"\d+\Z")
_LEARNING_ID_RE = re.compile(r"LEARNING_[A-Za-z0-9_-]+\Z")
_CITY_ID_RE = re.compile(r"\d+\Z")
_CITY_ACTION_RE = re.compile(
    r"fn_viewProgrm\(\s*['\"]([1-9]\d*)['\"]\s*,\s*"
    r"['\"]([1-9]\d*)['\"]\s*\);\s*return\s+false;?"
)
_CITY_DATES_RE = re.compile(
    r".*?(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2}).*?"
    r"(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2}).*"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_LOCAL_LIST_TITLE = "강좌목록&신청 | 부산광역시 사하구"
_LOCAL_HEADERS = (
    "강좌명", "접수상태", "모집인원", "접수인원", "대기인원", "학습대상", "접수방법"
)
_LOCAL_STATUS_MAP = {
    "접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "접수마감": "CLOSED",
    "종강": "CLOSED",
}
_AUDITED_LOCAL_TITLE_ATTRIBUTES: Mapping[str, tuple[str, str]] = {
    "3954": (
        '워라벨 프로젝트Ⅳ(신뢰,미덕,겸손,창의적 리더십)"메디치 가문에서 전하는 현대인 리더십코칭"',
        "워라벨 프로젝트Ⅳ(신뢰,미덕,겸손,창의적 리더십)",
    ),
    "3953": (
        '워라벨 프로젝트Ⅲ" 미리 만나보는 세계휴양지 TOP4 \'유럽 편\'"',
        "워라벨 프로젝트Ⅲ",
    ),
    "3952": (
        '워라벨 프로젝트Ⅱ " 건강한 여름나기! 핸드메이드 믹스청 만들기"',
        "워라벨 프로젝트Ⅱ",
    ),
}
_AUDITED_LOCAL_ORDER_INVERSIONS = (("5399", "5477"), ("5262", "5339"))
_LOCAL_DETAIL_SAFE = frozenset(
    {
        "강좌구분", "과정분류", "지역", "학습기관", "학습기간", "접수기간",
        "수강료", "교육방법", "교육대상", "교육주기", "교육정원", "접수방법",
        "교육장소", "URL",
    }
)
_LOCAL_DETAIL_SKIPPED = frozenset({"강사명", "문의전화", "상세내용", "강의계획서"})

_PLATFORM_REQUIRED = (
    "회차명", "강좌분류", "교육대상", "문의전화", "교육장소", "총 교육시간",
    "교육기간", "교육시간", "수강료", "재료비", "접수인원", "우선모집기간",
    "일반모집기간", "모집방법", "신청상태", "교육상태", "강좌소개",
    "강좌소개 첨부파일", "강사", "강의계획서", "결제방법", "주의사항",
    "검색키워드", "강좌제한",
)
_PLATFORM_OPTIONAL = frozenset(
    {"수강료 기타", "직장인 여부", "우선모집인원", "일반모집인원"}
)
_PLATFORM_SAFE = frozenset(
    {
        "강좌분류", "교육대상", "교육장소", "총 교육시간", "교육기간",
        "교육시간", "수강료", "재료비", "수강료 기타", "우선모집기간",
        "일반모집기간", "모집방법", "신청상태", "교육상태", "결제방법",
    }
)

_CITY_LIST_TITLE = "강좌/교육 : 부산광역시 통합예약"
_CITY_CARD_LABELS = ("기관", "대상", "장소", "일자", "방법", "문의")
_CITY_STATUS_MAP = {
    "대기중": "SCHEDULED", "접수대기": "SCHEDULED",
    "접수중": "OPEN", "접수마감": "CLOSED",
}
_CITY_DETAIL_REQUIRED = (
    "운영기간", "신청기간", "취소여부", "신청방법", "수강료", "요일 /시간",
    "문의전화", "운영기관", "대상",
)
_CITY_DETAIL_SAFE = frozenset(
    {"운영기간", "신청기간", "취소여부", "신청방법", "수강료", "요일 /시간", "대상", "운영기관"}
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanSahaContractError(f"expected one {label}, found {len(found)}")
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
        raise BusanSahaContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanSahaContractError(f"invalid {label}") from exc
    if result < 1:
        raise BusanSahaContractError(f"{label} must be positive")
    return result


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for match in _DATE_RE.finditer(_clean(value)):
        try:
            result.append(date(*(int(part) for part in match.groups())))
        except ValueError as exc:
            raise BusanSahaContractError("invalid source date") from exc
    return result


def _date_pair(value: Any, label: str) -> tuple[str, str]:
    found = _dates(value)
    if len(found) != 2 or found[1] < found[0]:
        raise BusanSahaContractError(f"{label} changed")
    return found[0].isoformat(), found[1].isoformat()


def _query_scope(final_url: str, host: str, path: str) -> Mapping[str, list[str]]:
    parsed = urlparse(_clean(final_url))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != host
        or parsed.port is not None
        or parsed.path != path
        or parsed.params or parsed.fragment or parsed.username or parsed.password
    ):
        raise BusanSahaContractError("response escaped the audited URL scope")
    return parse_qs(parsed.query, keep_blank_values=True)


def _compare_url(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() != "https" or not parsed.hostname or parsed.port is not None
        or parsed.params or parsed.fragment or parsed.username or parsed.password
    ):
        return ""
    query = urlencode(sorted((k, v) for k, vs in parse_qs(parsed.query, keep_blank_values=True).items() for v in vs))
    return f"https://{parsed.hostname.rstrip('.').lower()}{parsed.path}" + (f"?{query}" if query else "")


def is_busan_saha_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != BUSAN_SAHA_PROVIDER:
        return False
    return _compare_url(_target_value(target, "url")) in {
        _compare_url(BUSAN_SAHA_URL),
        _compare_url(BUSAN_SAHA_REGISTERED_URL),
    }


is_target = is_busan_saha_education_target


def busan_saha_list_url(page: int = 1) -> str:
    value = _positive_int(page, "page")
    return f"https://{BUSAN_SAHA_HOST}{BUSAN_SAHA_PATH}?" + urlencode(
        (("mId", BUSAN_SAHA_MID), ("page", value), ("lecType", "1"))
    )


def busan_saha_detail_url(sequence: Any) -> str:
    value = _clean(sequence)
    if not _SEQ_RE.fullmatch(value):
        raise BusanSahaContractError("invalid Saha sequence")
    return f"https://{BUSAN_SAHA_HOST}{BUSAN_SAHA_DETAIL_PATH}?" + urlencode(
        (("mId", BUSAN_SAHA_MID), ("seq", value))
    )


def canonical_busan_saha_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_SAHA_HOST
        or parsed.port not in (None, 80, 443)
        or parsed.path != BUSAN_SAHA_DETAIL_PATH
        or parsed.params or parsed.fragment or parsed.username or parsed.password
    ):
        return ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"mId", "seq"} or query.get("mId") != [BUSAN_SAHA_MID]:
        return ""
    seq = query.get("seq", [""])[0]
    return f"seq:{seq}" if _SEQ_RE.fullmatch(seq) else ""


def busan_saha_lifelong_list_url(office_code: str, page: int = 1) -> str:
    if office_code not in {code for code, _name in BUSAN_LIFELONG_SAHA_OFFICES}:
        raise BusanSahaContractError("invalid Saha platform office")
    value = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(office_code, value)
    payload["pageUnit"] = str(BUSAN_LIFELONG_PAGE_SIZE)
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_saha_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (("curPage", value), ("srchGugun", BUSAN_CITY_SAHA_GUGUN), ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE))
    )


def busan_saha_city_detail_url(group_id: Any, program_id: Any) -> str:
    group, program = _clean(group_id), _clean(program_id)
    if not _CITY_ID_RE.fullmatch(group) or not _CITY_ID_RE.fullmatch(program):
        raise BusanSahaContractError("invalid city course identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def _base_row(*, course_id: str, title: str, branch: str, branch_code: str) -> dict[str, Any]:
    return {
        "provider": BUSAN_SAHA_PROVIDER,
        "provider_course_id": course_id,
        "prefer_incoming_provider_course_id": True,
        "title": title,
        "description": title,
        "branch": branch,
        "branch_code": branch_code,
        "preserve_branch": True,
        "municipality_code": BUSAN_SAHA_MUNICIPALITY_CODE,
        "municipality_name": BUSAN_SAHA_MUNICIPALITY_NAME,
        "sido": "부산광역시",
        "sigungu": "사하구",
        "collection_category": "공공예약",
        "domain_category": "교육·강좌",
        "operator_type": "지자체/공공기관",
        "source_group": "municipal_reservation",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
    }


def _parse_local_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_total: Optional[int] = None,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, int]:
    query = _query_scope(final_url, BUSAN_SAHA_HOST, BUSAN_SAHA_PATH)
    if query != {"mId": [BUSAN_SAHA_MID], "page": [str(page)], "lecType": ["1"]}:
        raise BusanSahaContractError("Saha list response query changed")
    if _text(_one(soup.select("title"), "Saha list title")) != _LOCAL_LIST_TITLE:
        raise BusanSahaContractError("Saha list title changed")
    form = _one(soup.select("form#searchForm[name='searchForm']"), "Saha list form")
    if urlparse(_clean(form.get("action"))).path != BUSAN_SAHA_PATH:
        raise BusanSahaContractError("Saha list form action changed")
    for name, expected in (("page", str(page)), ("seq", "0"), ("lecType", "1")):
        field = _one(form.select(f"input[name='{name}']"), f"Saha {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSahaContractError(f"Saha form {name} changed")
    summary = _LOCAL_SUMMARY_RE.fullmatch(_text(_one(soup.select("div.board_edu_page"), "Saha summary")))
    if not summary or int(summary.group(1)) != page:
        raise BusanSahaContractError("Saha pagination summary changed")
    displayed, total = int(summary.group(2)), int(summary.group(3).replace(",", ""))
    data_last = max(1, math.ceil(total / 10))
    if displayed != data_last + 1:
        raise BusanSahaContractError("Saha displayed sentinel boundary changed")
    if expected_total is not None and total != expected_total:
        raise BusanSahaContractError("Saha total changed during census")
    if expected_last is not None and data_last != expected_last:
        raise BusanSahaContractError("Saha final page changed during census")
    table = _one(soup.select("table.tableSt_list"), "Saha course table")
    headers = tuple(_text(node) for node in table.select("thead th"))
    if headers != _LOCAL_HEADERS:
        raise BusanSahaContractError("Saha list headers changed")
    rows: list[dict[str, Any]] = []
    for position, tr in enumerate(table.select("tbody > tr"), 1):
        link = tr.select_one("td.class_name > a.className[onclick]")
        if link is None:
            if page == data_last + 1 and _clean(tr.get_text(" ", strip=True)) == "등록된 강좌가 없습니다.":
                continue
            raise BusanSahaContractError("Saha non-course table row")
        action = _LOCAL_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action or action.group(1) != str(page):
            raise BusanSahaContractError("Saha list identity action changed")
        sequence = action.group(2)
        title = _text(_one(link.select(":scope dl > dt"), "Saha course title"))
        title_attribute = _clean(link.get("title"))
        if not title or (
            title_attribute != title
            and _AUDITED_LOCAL_TITLE_ATTRIBUTES.get(sequence)
            != (title, title_attribute)
        ):
            raise BusanSahaContractError("Saha course title changed")
        values = [_text(node) for node in link.select(":scope dl > dd")]
        if len(values) != 3:
            raise BusanSahaContractError("Saha list value count changed")
        apply_start, apply_end = _date_pair(values[0].removeprefix("접수기간:"), "Saha application period")
        start, end = _date_pair(values[1].removeprefix("학습기간:"), "Saha education period")
        if not values[0].startswith("접수기간:") or not values[1].startswith("학습기간:") or not values[2].startswith("교육장소:"):
            raise BusanSahaContractError("Saha list labels changed")
        cells = tr.find_all("td", recursive=False)
        if len(cells) != 7:
            raise BusanSahaContractError("Saha list column count changed")
        source_status = _text(_one(cells[1].select("span.state"), "Saha status"))
        if source_status not in _LOCAL_STATUS_MAP:
            raise BusanSahaContractError("unknown Saha list status")
        numbers = tuple(_text(cell) for cell in cells[2:5])
        if any(not value.isdigit() for value in numbers):
            raise BusanSahaContractError("Saha capacity values changed")
        row = _base_row(
            course_id=f"{BUSAN_SAHA_PROVIDER}:local:{sequence}",
            title=title,
            branch="사하구평생학습관",
            branch_code="saha-lifelong-catalogue",
        )
        row.update(
            {
                "provider_organizer": "사하구청",
                "category": "평생학습",
                "program_type": "교육/강좌",
                "raw_url": busan_saha_detail_url(sequence),
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
                "target": _text(cells[5]),
                "venue_name": _clean(values[2].removeprefix("교육장소:")),
                "application_method_raw": _text(cells[6]),
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_SAHA_PARSER,
                    "source_catalog": "saha_lifelong_archive",
                    "source_identity": sequence,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "audited_title_attribute_anomaly": sequence in _AUDITED_LOCAL_TITLE_ATTRIBUTES,
                    "capacity": numbers[0],
                    "enrollment_values_never_persisted": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "applicant_list_fetched": False,
                    "service_family": "education",
                },
            }
        )
        rows.append(row)
    if page <= data_last:
        expected = 10 if page < data_last else total - 10 * (data_last - 1)
        if len(rows) != expected:
            raise BusanSahaContractError("Saha data page row count changed")
        end = _one(soup.select("div.box_page a.btn_end[onclick]"), "Saha final-page control")
        match = _LOCAL_END_RE.fullmatch(_clean(end.get("onclick")))
        if not match or int(match.group(1)) != data_last:
            raise BusanSahaContractError("Saha final-page control changed")
    elif page == data_last + 1:
        if rows or soup.select("div.box_page"):
            raise BusanSahaContractError("Saha sentinel changed")
    else:
        raise BusanSahaContractError("Saha request passed sentinel")
    return rows, total, data_last


def _local_detail_fields(table: Tag) -> tuple[dict[str, str], set[str]]:
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    labels: list[str] = []
    for tr in table.select("tbody > tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 1 and "title" in (cells[0].get("class") or []):
            continue
        if len(cells) % 2:
            raise BusanSahaContractError("Saha detail field columns changed")
        for index in range(0, len(cells), 2):
            if cells[index].name != "th" or cells[index + 1].name != "td":
                raise BusanSahaContractError("Saha detail field pairing changed")
            source_label = _text(cells[index])
            label = source_label.replace(" ", "")
            canonical = {
                "지역": "지역",
                "강사명": "강사명",
                "수강료": "수강료",
            }.get(label, source_label)
            if canonical in labels:
                raise BusanSahaContractError("duplicate Saha detail field")
            labels.append(canonical)
            if canonical in _LOCAL_DETAIL_SAFE:
                safe[canonical] = _text(cells[index + 1])
            elif canonical in _LOCAL_DETAIL_SKIPPED:
                skipped.add(canonical)
            else:
                raise BusanSahaContractError(f"unknown Saha detail field {canonical!r}")
    expected = _LOCAL_DETAIL_SAFE | _LOCAL_DETAIL_SKIPPED
    if set(labels) != expected or skipped != _LOCAL_DETAIL_SKIPPED:
        raise BusanSahaContractError("Saha detail field set changed")
    return safe, skipped


def _parse_local_detail(
    soup: BeautifulSoup,
    final_url: str,
    parent: Mapping[str, Any],
    *,
    cutoff: date,
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    sequence = _clean(raw.get("source_identity"))
    query = _query_scope(final_url, BUSAN_SAHA_HOST, BUSAN_SAHA_DETAIL_PATH)
    if query != {"mId": [BUSAN_SAHA_MID], "seq": [sequence]}:
        raise BusanSahaContractError("Saha detail response identity changed")
    if _text(_one(soup.select("title"), "Saha detail title")) != _LOCAL_LIST_TITLE:
        raise BusanSahaContractError("Saha detail title changed")
    form = _one(soup.select("form#listForm[name='listForm']"), "Saha detail list form")
    for name, expected in (
        ("page", _clean(raw.get("source_page"))),
        ("seq", sequence),
    ):
        field = _one(form.select(f"input[name='{name}']"), f"Saha detail {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSahaContractError("Saha detail hidden identity changed")
    table = _one(soup.select("table.table_view"), "Saha detail table")
    heading = _one(table.select("tr > th.title"), "Saha detail heading")
    source_status = _text(_one(heading.select(":scope span.state"), "Saha detail status"))
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")):
        raise BusanSahaContractError("Saha list/detail title mismatch")
    if source_status != _clean(raw.get("source_status")):
        raise BusanSahaContractError("Saha list/detail status mismatch")
    safe, skipped = _local_detail_fields(table)
    start, end = _date_pair(safe["학습기간"], "Saha detail education period")
    apply_start, apply_end = _date_pair(safe["접수기간"], "Saha detail application period")
    if (start, end) != (_clean(parent.get("start_date")), _clean(parent.get("end_date"))):
        raise BusanSahaContractError("Saha list/detail education dates mismatch")
    if (apply_start, apply_end) != (_clean(parent.get("apply_start")), _clean(parent.get("apply_end"))):
        raise BusanSahaContractError("Saha list/detail application dates mismatch")
    controls = soup.select("div.btn_area > a.btn[href*='fn_lec_receipt']")
    expected_action = f"javascript:fn_lec_receipt('{sequence}');"
    if len(controls) > 1 or any(
        _text(control) != "접수하기" or _clean(control.get("href")) != expected_action
        for control in controls
    ):
        raise BusanSahaContractError("Saha application control changed")
    list_status = _clean(raw.get("source_status"))
    active = bool(
        list_status == "접수중"
        and date.fromisoformat(apply_start) <= cutoff <= date.fromisoformat(apply_end)
        and len(controls) == 1
    )
    stale_suppressed = bool(controls and not active)
    if list_status == "접수중" and not active:
        raise BusanSahaContractError("open Saha course lacks an active exact control")
    if list_status == "접수예정" and controls:
        raise BusanSahaContractError("scheduled Saha course exposes a control")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": "ONLINE_RESERVATION" if active else "INFO_ONLY",
            "reservation_available": active,
            "status": _LOCAL_STATUS_MAP[list_status],
            "fee": safe["수강료"],
            "schedule_raw": safe["교육주기"],
            "target": safe["교육대상"],
            "venue_name": safe["교육장소"],
            "application_method_raw": safe["접수방법"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": active,
        "closed_stale_application_control_suppressed": stale_suppressed,
        "contact_value_never_read": "문의전화" in skipped,
        "instructor_value_never_read": "강사명" in skipped,
        "free_form_detail_never_read": "상세내용" in skipped,
        "attachments_never_read": "강의계획서" in skipped,
        "application_control_target_never_persisted": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _platform_offices() -> tuple[_lifelong.BusanOffice, ...]:
    result: list[_lifelong.BusanOffice] = []
    for code, name in BUSAN_LIFELONG_SAHA_OFFICES:
        office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(code)
        if office is None or office.name != name:
            raise BusanSahaContractError("Saha platform office changed")
        municipal_state = bool(
            office.ownership == "municipal"
            and office.municipality_code == BUSAN_SAHA_MUNICIPALITY_CODE
            and office.municipality_name == BUSAN_SAHA_MUNICIPALITY_NAME
        )
        dedicated_state = bool(
            office.ownership == "duplicate_dedicated_saha_owner"
            and not office.municipality_code
            and not office.municipality_name
        )
        if not (municipal_state or dedicated_state):
            raise BusanSahaContractError("Saha platform ownership changed")
        result.append(office)
    return tuple(result)


def _parse_platform_page(
    soup: BeautifulSoup,
    _final_url: str,
    *,
    office: _lifelong.BusanOffice,
    page: int,
) -> tuple[list[dict[str, Any]], int]:
    errors = _lifelong._form_errors(soup, office, page)
    if errors:
        raise BusanSahaContractError("; ".join(errors))
    last, errors = _lifelong._advertised_last(soup)
    if errors or last != 1:
        raise BusanSahaContractError("Saha platform pageUnit1000 boundary changed")
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if errors:
        raise BusanSahaContractError("; ".join(errors))
    if page == 1 and office.code == "OFFICE_00002632" and not rows:
        raise BusanSahaContractError("Saha platform primary office became empty")
    if page == 2 and rows:
        raise BusanSahaContractError("Saha platform sentinel is not empty")
    if page not in {1, 2}:
        raise BusanSahaContractError("Saha platform request passed sentinel")
    return rows, last


def _platform_signature(rows: Sequence[Mapping[str, Any]]) -> Counter[tuple[str, ...]]:
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


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    office_code = _clean(raw.get("source_office_code"))
    office_name = _clean(raw.get("source_office_name"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(identity):
        raise BusanSahaContractError("invalid native Saha platform identity")
    if (office_code, office_name) not in BUSAN_LIFELONG_SAHA_OFFICES:
        raise BusanSahaContractError("native platform row left Saha owner")
    result = dict(row)
    result.update(
        _base_row(
            course_id=f"{BUSAN_SAHA_PROVIDER}:lifelong:{identity}",
            title=_clean(row.get("title")),
            branch=office_name,
            branch_code=f"saha-lifelong-{office_code.lower()}",
        )
    )
    result.update(
        {
            "provider_organizer": office_name,
            "collection_type": "complete_shared_office_census+native_current_detail",
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_SAHA_PARSER,
        "source_catalog": "busan_lifelong_saha_native",
        "source_provider": BUSAN_LIFELONG_PROVIDER,
        "detail_verified": False,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
        "service_family": "education",
    }
    return result


def _platform_detail_values(soup: BeautifulSoup) -> tuple[dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in soup.select("div.form_group dl"):
        heading = _one(definition.find_all("dt", recursive=False), "platform detail label")
        value = _one(definition.find_all("dd", recursive=False), "platform detail value")
        label = _text(heading)
        if label in labels:
            raise BusanSahaContractError("duplicate platform detail field")
        labels.append(label)
        if label in _PLATFORM_SAFE:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = tuple(label for label in labels if label not in _PLATFORM_OPTIONAL)
    if required != _PLATFORM_REQUIRED:
        raise BusanSahaContractError("platform detail field order changed")
    return safe, skipped


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    office_code = _clean(raw.get("source_office_code"))
    office_name = _clean(raw.get("source_office_name"))
    query = _query_scope(final_url, _lifelong.BUSAN_LIFELONG_HOST, BUSAN_LIFELONG_DETAIL_PATH)
    if query != {"lng_id": [identity]}:
        raise BusanSahaContractError("platform detail response identity changed")
    for name, expected in (("lng_id", identity), ("inst_id", office_code)):
        fields = {_clean(node.get("value")) for node in soup.select(f"input[name='{name}']")}
        if fields != {expected}:
            raise BusanSahaContractError(f"platform detail {name} changed")
    heading = _one(soup.select("h2.enrolTit"), "platform detail title")
    prefix = _text(_one(heading.select(":scope > span"), "platform office prefix"))
    if prefix != f"[{office_name}]":
        raise BusanSahaContractError("platform detail office changed")
    direct_title = _clean(
        " ".join(str(child) for child in heading.children if isinstance(child, NavigableString))
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanSahaContractError("platform list/detail title mismatch")
    safe, skipped = _platform_detail_values(soup)
    for label in ("교육대상", "교육장소", "교육기간", "교육시간", "수강료", "모집방법", "신청상태"):
        if not safe.get(label):
            raise BusanSahaContractError("platform safe detail value is empty")
    start, end = _date_pair(safe["교육기간"], "platform education period")
    if (start, end) != (_clean(parent.get("start_date")), _clean(parent.get("end_date"))):
        raise BusanSahaContractError("platform list/detail dates mismatch")
    controls = soup.select("#learning_aply_btn")
    if len(controls) > 1:
        raise BusanSahaContractError("multiple platform application controls")
    control_label = _text(controls[0]) if controls else ""
    detail_status = safe["신청상태"]
    active = bool(
        len(controls) == 1
        and "접수중" in detail_status
        and _clean(controls[0].get("onclick")) == "fn_learning_apply(); return false;"
        and control_label in {"일반모집신청", "대기자신청", "우선모집신청"}
    )
    if controls and not active:
        raise BusanSahaContractError("platform application control/status changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": ("WAITLIST_APPLY" if active and control_label == "대기자신청" else "ONLINE_RESERVATION" if active else "INFO_ONLY"),
            "reservation_available": active,
            "status": "OPEN" if active else "SCHEDULED" if "접수대기" in detail_status else "CLOSED",
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
        "detail_source_status": detail_status,
        "contact_value_never_read": "문의전화" in skipped,
        "instructor_value_never_read": "강사" in skipped,
        "enrollment_values_never_read": "접수인원" in skipped,
        "attachments_never_read": {"강좌소개 첨부파일", "강의계획서"}.issubset(skipped),
        "free_form_values_never_read": {"강좌소개", "주의사항", "검색키워드", "강좌제한"}.issubset(skipped),
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_contract(
    soup: BeautifulSoup, final_url: str, *, page: int
) -> tuple[int, Optional[Tag]]:
    query = _query_scope(final_url, BUSAN_CITY_HOST, BUSAN_CITY_LIST_PATH)
    if query != {
        "curPage": [str(page)],
        "srchGugun": [BUSAN_CITY_SAHA_GUGUN],
        "srchResveInsttCd": [BUSAN_CITY_RESIDENT_OFFICE],
    }:
        raise BusanSahaContractError("city list response query changed")
    if _text(_one(soup.select("title"), "city list title")) != _CITY_LIST_TITLE:
        raise BusanSahaContractError("city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if _clean(form.get("method")).casefold() != "get" or urlparse(_clean(form.get("action"))).path != "/lctre":
        raise BusanSahaContractError("city search form changed")
    field = _one(form.select("input[name='curPage']"), "city page field")
    if _clean(field.get("value")) != str(page):
        raise BusanSahaContractError("city page field changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_SAHA_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanSahaContractError(f"city {name} filter changed")
    roots = soup.select("ul.reserveList")
    ends = soup.select("div.paginate > a.pgEnd[href]")
    if not roots and not ends:
        if _text(_one(soup.select("div.paginate"), "empty city pagination")):
            raise BusanSahaContractError("empty city pagination changed")
        return 0, None
    root = _one(roots, "city reserve list") if roots else None
    end = _one(ends, "city final-page control")
    parsed = urlparse(urljoin(BUSAN_CITY_SAHA_URL, _clean(end.get("href"))))
    end_query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        (parsed.hostname or "").lower() != BUSAN_CITY_HOST
        or parsed.path != BUSAN_CITY_LIST_PATH
        or set(end_query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or end_query.get("srchGugun") != [BUSAN_CITY_SAHA_GUGUN]
        or end_query.get("srchResveInsttCd") != [BUSAN_CITY_RESIDENT_OFFICE]
        or len(end_query.get("curPage", [])) != 1
        or not end_query["curPage"][0].isdigit()
    ):
        raise BusanSahaContractError("unsafe city final-page control")
    return int(end_query["curPage"][0]), root


def _city_date_ranges(value: Any) -> tuple[str, str, str, str]:
    match = _CITY_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanSahaContractError("city card dates changed")
    parts = tuple(date.fromisoformat(part).isoformat() for part in match.groups())
    if parts[1] < parts[0] or parts[3] < parts[2]:
        raise BusanSahaContractError("city card date range reversed")
    return parts


def _parse_city_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_contract(soup, final_url, page=page)
    if expected_last is not None and last != expected_last:
        raise BusanSahaContractError("city final page changed")
    if last == 0:
        return [], 0
    if page > last:
        if page != last + 1 or root is not None:
            raise BusanSahaContractError("city sentinel changed")
        return [], last
    rows: list[dict[str, Any]] = []
    for position, item in enumerate(root.find_all("li", recursive=False) if root else [], 1):
        link = _one(item.select(":scope > a.reserveItem[onclick]"), "city course link")
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanSahaContractError("city identity action changed")
        group_id, program_id = action.groups()
        title_node = _one(link.select(":scope .tit"), "city course title")
        title = _text(title_node)
        if not title or _clean(title_node.get("title")) not in {"", title}:
            raise BusanSahaContractError("city course title changed")
        source_status = _text(_one(link.select(":scope .statusMark"), "city status"))
        if source_status not in _CITY_STATUS_MAP:
            raise BusanSahaContractError("unknown city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanSahaContractError("city card labels changed")
        safe = {label: _text(value) for label, value in zip(labels[:-1], values[:-1])}
        if any(not value for value in safe.values()):
            raise BusanSahaContractError("city safe card value empty")
        branch = safe["기관"]
        if not branch.startswith("사하구 ") or not branch.endswith(" 주민자치회"):
            raise BusanSahaContractError("city row left Saha owner")
        apply_start, apply_end, start, end = _city_date_ranges(safe["일자"])
        method = ", ".join(part for part in (_clean(part) for part in safe["방법"].split(",")) if part)
        row = _base_row(
            course_id=f"{BUSAN_SAHA_PROVIDER}:reserve:{group_id}:{program_id}",
            title=title,
            branch=branch,
            branch_code=f"saha-reserve-{group_id}",
        )
        row.update(
            {
                "provider_organizer": branch,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": busan_saha_city_detail_url(group_id, program_id),
                "application_url": "",
                "application_type": "INFO_ONLY",
                "application_method_raw": method,
                "reservation_available": False,
                "status": _CITY_STATUS_MAP[source_status],
                "period": f"{start} ~ {end}",
                "start_date": start,
                "end_date": end,
                "apply_period": f"{apply_start} ~ {apply_end}",
                "apply_start": apply_start,
                "apply_end": apply_end,
                "target": safe["대상"],
                "venue_name": safe["장소"],
                "fee": "",
                "schedule_raw": "",
                "collection_type": "complete_html_pages+current_detail_allowlist",
                "raw_fields": {
                    "parser": BUSAN_SAHA_PARSER,
                    "source_catalog": "busan_reserve_saha_resident_councils",
                    "source_identity": f"{group_id}:{program_id}",
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": method,
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "applicant_list_fetched": False,
                    "service_family": "education",
                },
            }
        )
        rows.append(row)
    if not rows:
        raise BusanSahaContractError("city data page became empty")
    return rows, last


def _city_detail_values(info: Tag) -> tuple[dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(definition.find_all("dt", recursive=False), "city detail label")
        value = _one(definition.find_all("dd", recursive=False), "city detail value")
        label = _text(heading)
        if label in labels:
            raise BusanSahaContractError("duplicate city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE:
            safe[label] = _text(value)
        elif label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        else:
            raise BusanSahaContractError(f"unknown city detail field {label!r}")
    without_attachment = tuple(label for label in labels if label != "첨부파일")
    if without_attachment != _CITY_DETAIL_REQUIRED or "문의전화" not in skipped:
        raise BusanSahaContractError("city detail field order changed")
    return safe, skipped


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    query = _query_scope(final_url, BUSAN_CITY_HOST, BUSAN_CITY_DETAIL_PATH)
    if query != {"resveGroupSn": [group_id], "progrmSn": [program_id]}:
        raise BusanSahaContractError("city detail response identity changed")
    if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
        raise BusanSahaContractError("city detail title changed")
    form = _one(soup.select("form#viewForm"), "city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(form.get("action")):
        raise BusanSahaContractError("city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanSahaContractError("city detail identity changed")
    heading = _one(form.select(":scope > div.contHeader > h3.titPage"), "city detail heading")
    source_status = _text(_one(heading.select(":scope .statusMark"), "city detail status"))
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select("span"):
        node.extract()
    if _clean(clone.get_text(" ", strip=True)) != _clean(parent.get("title")) or source_status != _clean(raw.get("source_status")):
        raise BusanSahaContractError("city list/detail heading mismatch")
    info = _one(form.select(":scope > div.reserveStateWrap div.reserveStateInfo"), "city detail values")
    safe, skipped = _city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE):
        raise BusanSahaContractError("city safe detail value empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanSahaContractError("city free-form boundary changed")
    start, end = _date_pair(safe["운영기간"], "city operating period")
    apply_start, apply_end = _date_pair(safe["신청기간"], "city application period")
    if (start, end, apply_start, apply_end) != (
        _clean(parent.get("start_date")), _clean(parent.get("end_date")),
        _clean(parent.get("apply_start")), _clean(parent.get("apply_end")),
    ):
        raise BusanSahaContractError("city list/detail dates mismatch")
    if safe["신청방법"] != _clean(raw.get("source_application_method")) or safe["운영기관"] != _clean(parent.get("branch")) or safe["대상"] != _clean(parent.get("target")):
        raise BusanSahaContractError("city list/detail safe values mismatch")
    controls = form.select(":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL")
    if len(controls) > 1:
        raise BusanSahaContractError("multiple city controls")
    label = _text(controls[0]) if controls else ""
    status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    app_type = "INFO_ONLY"
    if status == "OPEN" and "온라인" in method:
        if len(controls) != 1 or not any(token in label for token in ("신청", "예약")):
            raise BusanSahaContractError("open online city row lacks control")
        active, app_type = True, "ONLINE_RESERVATION"
    elif status == "OPEN" and any(token in method for token in ("방문", "전화")):
        if label not in {"", "방문예약"}:
            raise BusanSahaContractError("offline city control changed")
        app_type = "OFFLINE_APPLY"
    elif status == "CLOSED" and label not in {"", "접수마감"}:
        raise BusanSahaContractError("closed city control changed")
    elif status == "SCHEDULED" and label not in {"", "대기중", "접수대기"}:
        raise BusanSahaContractError("scheduled city control changed")
    result = dict(parent)
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": app_type,
            "reservation_available": active,
            "fee": safe["수강료"],
            "schedule_raw": safe["요일 /시간"],
        }
    )
    result["raw_fields"] = {
        **raw,
        "detail_verified": True,
        "detail_application_control": bool(active),
        "inquiry_value_never_read": True,
        "attachments_never_read": "첨부파일" in skipped,
        "free_form_detail_never_read": True,
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


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
                raise BusanSahaContractError("max_requests cap reached")
            self.count += 1


def _response_soup(response: Any, requested_url: str) -> tuple[BeautifulSoup, str]:
    try:
        status = int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        status = 0
    if status != 200:
        raise _TransientFetchError(f"unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise BusanSahaContractError("redirects are not accepted")
    final_url = _clean(getattr(response, "url", "")) or requested_url
    if _compare_url(final_url) != _compare_url(requested_url):
        raise BusanSahaContractError("response URL changed")
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", None)
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content:
        raise _TransientFetchError("empty HTML response")
    if len(content) > BUSAN_SAHA_MAX_HTML_BYTES:
        raise BusanSahaContractError("HTML response exceeded audited size")
    soup = BeautifulSoup(content, "lxml")
    if soup.select_one("div#error, div.error, .errorPage") and not soup.select("table, ul.reserveList"):
        raise _TransientFetchError("transient source error page")
    return soup, final_url


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
    for attempt in range(BUSAN_SAHA_FETCH_ATTEMPTS):
        session = session_factory()
        sessions += 1
        try:
            budget.take()
            response = fetcher(session, url, timeout)
            soup, final_url = _response_soup(response, url)
            return _FetchResult(parser(soup, final_url), attempt, sessions)
        except BusanSahaContractError:
            raise
        except Exception as exc:
            last = exc
            if attempt + 1 < BUSAN_SAHA_FETCH_ATTEMPTS:
                sleeper(min(0.15 * (2**attempt), 0.5))
        finally:
            _close_quietly(session)
    raise BusanSahaContractError(f"fetch failed for {url}: {_clean(last)}")


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

    if not items:
        return values, retries, sessions
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        futures = [pool.submit(run, item) for item in items]
        for future in as_completed(futures):
            key, result = future.result()
            values[key] = result.value
            retries += result.retries
            sessions += result.sessions
    return values, retries, sessions


def _signature(rows: Sequence[Mapping[str, Any]]) -> str:
    values = tuple(
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    if lowered.endswith(("_never_read", "_never_persisted", "_never_persisted")):
        return False
    return any(
        token in lowered
        for token in (
            "phone", "telephone", "email", "instructor", "teacher", "강사", "전화",
            "메일", "applicant", "loginid", "password",
        )
    )


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
        if isinstance(value, set):
            return sorted(visit(item) for item in value)
        if isinstance(value, str):
            text, phones = _PHONE_RE.subn("[redacted]", value)
            text, emails = _EMAIL_RE.subn("[redacted]", text)
            redactions += phones + emails
            return text
        return value

    return visit(row), redactions


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
        "detail_pages": 0,
        "network_requests": 0,
        "network_retry_count": 0,
        "sessions_created": 0,
        "local_source_rows": 0,
        "local_data_pages": 0,
        "local_current_count": 0,
        "platform_source_rows": 0,
        "platform_external_duplicate_rows": 0,
        "platform_native_rows": 0,
        "platform_native_current_count": 0,
        "city_source_rows": 0,
        "city_current_count": 0,
        "source_total": 0,
        "duplicate_source_rows": 0,
        "unique_education_source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "pii_redaction_count": 0,
        "configured_collection_error": "",
        "ownership_scope": BUSAN_SAHA_OWNERSHIP_SCOPE,
        "owner_boundary_audit": BUSAN_SAHA_OWNER_BOUNDARY_AUDIT,
        "discovery_audit": BUSAN_SAHA_DISCOVERY_AUDIT,
    }


def collect_busan_saha_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 150,
    detail_limit: int = 180,
    max_requests: int = 240,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = BUSAN_SAHA_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one fail-closed current/future snapshot of all Saha ledgers."""

    meta = _base_meta()
    if not is_busan_saha_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the exact canonical Busan Saha education owner"
        )
        return [], BUSAN_SAHA_PARSER, meta
    try:
        if any(isinstance(value, bool) for value in (timeout, max_pages, detail_limit, max_requests, max_workers)):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        request_cap = max(0, int(max_requests))
        workers = min(max(1, int(max_workers)), BUSAN_SAHA_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = f"invalid limits/today: {_clean(exc)}"
        return [], BUSAN_SAHA_PARSER, meta
    if page_cap < 1 or request_cap < 3:
        meta["source_cap_reached"] = True
        meta["configured_collection_error"] = "caps cannot inspect all Saha ledgers"
        return [], BUSAN_SAHA_PARSER, meta

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
        local_first, local_total, local_last = fetch_one(
            busan_saha_list_url(1),
            lambda soup, final: _parse_local_page(soup, final, page=1),
            list_phase=True,
        )
        if local_last > page_cap:
            raise BusanSahaContractError(f"max_pages cap allows {page_cap} of {local_last} local pages")
        local_pages: dict[int, list[dict[str, Any]]] = {1: local_first}
        if local_last > 1:
            local_pages.update(
                fetch_batch(
                    [
                        (
                            page,
                            busan_saha_list_url(page),
                            lambda soup, final, p=page: _parse_local_page(
                                soup, final, page=p, expected_total=local_total, expected_last=local_last
                            )[0],
                        )
                        for page in range(2, local_last + 1)
                    ],
                    list_phase=True,
                )
            )
        local_empty, _total, _last = fetch_one(
            busan_saha_list_url(local_last + 1),
            lambda soup, final: _parse_local_page(
                soup, final, page=local_last + 1, expected_total=local_total, expected_last=local_last
            ),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if local_empty:
            raise BusanSahaContractError("Saha sentinel returned rows")
        recheck_pages = sorted({1, local_last})
        rechecked = fetch_batch(
            [
                (
                    page,
                    busan_saha_list_url(page),
                    lambda soup, final, p=page: _parse_local_page(
                        soup, final, page=p, expected_total=local_total, expected_last=local_last
                    )[0],
                )
                for page in recheck_pages
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(recheck_pages)
        for page in recheck_pages:
            if _signature(rechecked[page]) != _signature(local_pages[page]):
                raise BusanSahaContractError("Saha boundary page changed")
        local_rows = [row for page in range(1, local_last + 1) for row in local_pages[page]]
        if len(local_rows) != local_total:
            raise BusanSahaContractError("Saha complete source count changed")
        local_ids = [_clean(row.get("raw_fields", {}).get("source_identity")) for row in local_rows]
        inversions = tuple(
            (previous, current)
            for previous, current in zip(local_ids, local_ids[1:])
            if int(current) >= int(previous)
        )
        if len(local_ids) != len(set(local_ids)) or (
            len(local_ids) == BUSAN_SAHA_DISCOVERY_AUDIT["local_rows"]
            and inversions != _AUDITED_LOCAL_ORDER_INVERSIONS
        ) or (
            len(local_ids) != BUSAN_SAHA_DISCOVERY_AUDIT["local_rows"]
            and inversions
        ):
            raise BusanSahaContractError("Saha source identity order changed")
        local_by_id = {identity: row for identity, row in zip(local_ids, local_rows)}

        platform_source: list[dict[str, Any]] = []
        platform_counts: dict[str, int] = {}
        for office in _platform_offices():
            censuses: list[list[dict[str, Any]]] = []
            for census_index in range(2):
                page_rows, _ = fetch_one(
                    busan_saha_lifelong_list_url(office.code, 1),
                    lambda soup, final, owner=office: _parse_platform_page(soup, final, office=owner, page=1),
                    list_phase=True,
                )
                empty, _ = fetch_one(
                    busan_saha_lifelong_list_url(office.code, 2),
                    lambda soup, final, owner=office: _parse_platform_page(soup, final, office=owner, page=2),
                    list_phase=True,
                )
                meta["sentinel_requests"] += 1
                if empty:
                    raise BusanSahaContractError("platform sentinel returned rows")
                if census_index:
                    meta["stability_rechecks"] += 2
                censuses.append(page_rows)
            if _platform_signature(censuses[0]) != _platform_signature(censuses[1]):
                raise BusanSahaContractError("platform complete census changed")
            sequences = sorted(int(row["raw_fields"]["list_sequence"]) for row in censuses[0])
            if sequences != list(range(1, len(censuses[0]) + 1)):
                raise BusanSahaContractError("platform source sequence changed")
            platform_counts[office.code] = len(censuses[0])
            platform_source.extend(censuses[0])
        external_rows = [row for row in platform_source if row.get("raw_fields", {}).get("identity_kind") == "external"]
        native_source = [row for row in platform_source if row.get("raw_fields", {}).get("identity_kind") == "internal"]
        if len(external_rows) + len(native_source) != len(platform_source):
            raise BusanSahaContractError("unexpected platform identity family")
        external_ids: list[str] = []
        for row in external_rows:
            identity = canonical_busan_saha_course_identity(row.get("raw_url"))
            if not identity.startswith("seq:"):
                raise BusanSahaContractError("platform external row left exact Saha route")
            seq = identity.removeprefix("seq:")
            local = local_by_id.get(seq)
            if local is None or (
                _clean(row.get("title")), _clean(row.get("start_date")), _clean(row.get("end_date"))
            ) != (
                _clean(local.get("title")), _clean(local.get("start_date")), _clean(local.get("end_date"))
            ):
                raise BusanSahaContractError("platform external row does not match canonical Saha identity")
            external_ids.append(seq)
        if len(external_ids) != len(set(external_ids)):
            raise BusanSahaContractError("duplicate external Saha seq")
        native_rows = [_platform_native_row(row) for row in native_source]
        native_ids = [_clean(row.get("provider_course_id")) for row in native_rows]
        if len(native_ids) != len(set(native_ids)):
            raise BusanSahaContractError("duplicate native Saha platform identity")

        city_first, city_last = fetch_one(
            busan_saha_city_list_url(1),
            lambda soup, final: _parse_city_page(soup, final, page=1),
            list_phase=True,
        )
        if city_last > page_cap:
            raise BusanSahaContractError(f"max_pages cap allows {page_cap} of {city_last} city pages")
        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first}
        if city_last > 1:
            city_pages.update(
                fetch_batch(
                    [
                        (
                            page,
                            busan_saha_city_list_url(page),
                            lambda soup, final, p=page: _parse_city_page(soup, final, page=p, expected_last=city_last)[0],
                        )
                        for page in range(2, city_last + 1)
                    ],
                    list_phase=True,
                )
            )
        sentinel_page = city_last + 1 if city_last else 2
        city_empty, sentinel_last = fetch_one(
            busan_saha_city_list_url(sentinel_page),
            lambda soup, final: _parse_city_page(soup, final, page=sentinel_page, expected_last=city_last),
            list_phase=True,
        )
        meta["sentinel_requests"] += 1
        if city_empty or sentinel_last != city_last:
            raise BusanSahaContractError("city sentinel changed")
        city_recheck_pages = sorted({1, city_last} - {0})
        city_rechecked = fetch_batch(
            [
                (
                    page,
                    busan_saha_city_list_url(page),
                    lambda soup, final, p=page: _parse_city_page(soup, final, page=p, expected_last=city_last)[0],
                )
                for page in city_recheck_pages
            ],
            list_phase=True,
        )
        meta["stability_rechecks"] += len(city_recheck_pages)
        for page in city_recheck_pages:
            if _signature(city_rechecked[page]) != _signature(city_pages[page]):
                raise BusanSahaContractError("city boundary page changed")
        city_rows = [row for page in range(1, city_last + 1) for row in city_pages.get(page, [])]
        city_ids = [_clean(row.get("provider_course_id")) for row in city_rows]
        if len(city_ids) != len(set(city_ids)):
            raise BusanSahaContractError("duplicate city identity")

        local_current = [row for row in local_rows if date.fromisoformat(row["end_date"]) >= cutoff]
        native_current = [row for row in native_rows if date.fromisoformat(row["end_date"]) >= cutoff]
        city_current = [row for row in city_rows if date.fromisoformat(row["end_date"]) >= cutoff]
        current = local_current + native_current + city_current
        if len(current) > detail_cap:
            raise BusanSahaContractError(f"detail_limit cap allows {detail_cap} of {len(current)} current details")
        detail_items: list[tuple[str, str, Callable[[BeautifulSoup, str], dict[str, Any]]]] = []
        for row in local_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]),
                    _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_local_detail(soup, final, parent, cutoff=cutoff),
                )
            )
        for row in native_current:
            detail_items.append(
                (
                    _clean(row["provider_course_id"]),
                    _clean(row["raw_url"]),
                    lambda soup, final, parent=row: _parse_platform_detail(soup, final, parent),
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
            safe, count = _sanitize_row(row)
            sanitized.append(safe)
            redactions += count
        result = list((dedupe_rows or _default_dedupe)(sanitized))
        before = Counter(_clean(row.get("provider_course_id")) for row in sanitized)
        after = Counter(_clean(row.get("provider_course_id")) for row in result)
        if len(result) != len(sanitized) or before != after or len(after) != len(result):
            raise BusanSahaContractError("dedupe changed the complete identity set")
        meta.update(
            {
                "local_source_rows": len(local_rows),
                "local_data_pages": local_last,
                "local_current_count": len(local_current),
                "local_expired_count": len(local_rows) - len(local_current),
                "local_source_status_counts": dict(Counter(row["raw_fields"]["source_status"] for row in local_rows)),
                "platform_source_rows": len(platform_source),
                "platform_rows_by_office": platform_counts,
                "platform_external_duplicate_rows": len(external_rows),
                "platform_external_matching_canonical": len(external_ids),
                "platform_native_rows": len(native_rows),
                "platform_native_current_count": len(native_current),
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "source_total": len(local_rows) + len(platform_source) + len(city_rows),
                "duplicate_source_rows": len(external_rows),
                "unique_education_source_rows": len(local_rows) + len(native_rows) + len(city_rows),
                "current_source_count": len(current),
                "returned_count": len(result),
                "status_counts": dict(Counter(row.get("status") for row in result)),
                "application_control_count": sum(bool(row.get("reservation_available")) for row in result),
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
                "configured_collection_error": "",
            }
        )
        return result, BUSAN_SAHA_PARSER, meta
    except Exception as exc:
        meta["network_requests"] = budget.count
        message = _clean(exc)
        if "cap" in message:
            meta["source_cap_reached"] = True
        meta["configured_collection_error"] = message or exc.__class__.__name__
        return [], BUSAN_SAHA_PARSER, meta


collect_courses = collect_busan_saha_education


__all__ = [
    "BUSAN_SAHA_PROVIDER",
    "BUSAN_SAHA_CANDIDATE_ID",
    "BUSAN_SAHA_MUNICIPALITY_CODE",
    "BUSAN_SAHA_MUNICIPALITY_NAME",
    "BUSAN_SAHA_URL",
    "BUSAN_SAHA_CANONICAL_URL",
    "BUSAN_SAHA_REGISTERED_URL",
    "BUSAN_SAHA_RESERVATION_ALIAS_URL",
    "BUSAN_CITY_SAHA_URL",
    "BUSAN_LIFELONG_SAHA_OFFICES",
    "BUSAN_SAHA_PARSER",
    "BUSAN_SAHA_OWNERSHIP_SCOPE",
    "BUSAN_SAHA_OWNER_BOUNDARY_AUDIT",
    "BUSAN_SAHA_DISCOVERY_AUDIT",
    "BusanSahaContractError",
    "busan_saha_list_url",
    "busan_saha_detail_url",
    "busan_saha_lifelong_list_url",
    "busan_saha_city_list_url",
    "busan_saha_city_detail_url",
    "canonical_busan_saha_course_identity",
    "is_busan_saha_education_target",
    "is_target",
    "collect_busan_saha_education",
    "collect_courses",
]
