"""Atomic collector for Yeonsu-gu resident-centre education.

The canonical owner is the district's 주민자치센터 programme ledger at
``/edu/sub/apply.asp``.  Its public education-date search is a *start-date*
filter, not an overlap filter.  The collector therefore scans two disjoint
partitions: the complete previous calendar year (a rollover guard) and every
course starting from January 1 of the current year through 2099.  List dates
identify possible current/future rows; every such row is then checked against
the full-year dates on its detail page.

Each partition must expose every advertised page, an immediately empty
sentinel, and stable first/last boundaries.  Application links are inspected
but never followed.  Detail values for contacts, instructors, descriptions,
attachments, payment/refund notes and applicant data are deliberately never
read.

The district also operates a lifelong-learning search and a culture-portal
education ledger.  They are separate owners, as are the Incheon city portal
and library systems; this module does not fan out into any of them.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag


YEONSU_PROVIDER = "MUNI_WWW_YEONSU_GO_KR_CB4C41BB"
YEONSU_CANONICAL_CANDIDATE_ID = "MUNI_IR_876F7A68981B"
YEONSU_LEGACY_DETAIL_CANDIDATE_ID = "MUNI_IR_AECABD41D5B5"
YEONSU_MUNICIPALITY_CODE = "2818500000"
YEONSU_MUNICIPALITY_NAME = "인천광역시 연수구"

YEONSU_HOST = "www.yeonsu.go.kr"
YEONSU_LIST_PATH = "/edu/sub/apply.asp"
YEONSU_CANONICAL_URL = f"https://{YEONSU_HOST}{YEONSU_LIST_PATH}"
YEONSU_URL = YEONSU_CANONICAL_URL
YEONSU_LEGACY_DETAIL_URL = (
    f"{YEONSU_CANONICAL_URL}?page=v&lec_idx=43490"
)
YEONSU_PAGE_SIZE = 12
YEONSU_FUTURE_END = date(2099, 12, 31)
YEONSU_FETCH_ATTEMPTS = 3
YEONSU_MAX_WORKERS = 8
YEONSU_MAX_HTML_BYTES = 2_000_000
YEONSU_PARSER = (
    "yeonsu_resident_education_previous_year_rollover+current_future_start_"
    "partition+complete_pages+empty_sentinels+stable_boundaries+all_current_"
    "safe_details+identity_bound_application_controls+exact_reversed_period_"
    "exclusions+pii_never_read"
)
YEONSU_OWNERSHIP_SCOPE = (
    "yeonsu_official_resident_centre_online_programme_ledger_only"
)


class YeonsuContractError(ValueError):
    """Raised when an audited Yeonsu source contract changes."""


@dataclass(frozen=True)
class YeonsuPartition:
    key: str
    start: date
    end: date


YEONSU_OWNER_BOUNDARY_AUDIT: Mapping[str, Mapping[str, Any]] = {
    YEONSU_CANONICAL_CANDIDATE_ID: {
        "decision": "canonical_complete_resident_centre_owner",
        "provider": YEONSU_PROVIDER,
        "url": YEONSU_CANONICAL_URL,
        "owner": YEONSU_PROVIDER,
    },
    YEONSU_LEGACY_DETAIL_CANDIDATE_ID: {
        "decision": "registered_single_detail_alias_retarget_to_list",
        "provider": YEONSU_PROVIDER,
        "url": YEONSU_LEGACY_DETAIL_URL,
        "owner": YEONSU_PROVIDER,
    },
    "MUNI_IR_13A1327A5249": {
        "decision": "separate_lifelong_integrated_search_owner",
        "provider": "MUNI_WWW_YEONSU_GO_KR_AFBFF9A3",
        "url": "https://www.yeonsu.go.kr/lll/institution/edu_local.asp",
        "owner": "MUNI_WWW_YEONSU_GO_KR_AFBFF9A3",
    },
    "MUNI_IR_F1A32FCD318C": {
        "decision": "separate_culture_portal_education_owner",
        "provider": "MUNI_WWW_YEONSU_GO_KR_EE28521E",
        "url": "https://www.yeonsu.go.kr/culture/edu/lecture/reservation.asp",
        "owner": "MUNI_WWW_YEONSU_GO_KR_EE28521E",
    },
    "MUNI_IR_26D3873315F3": {
        "decision": "separate_culture_institute_owner",
        "provider": "MUNI_WWW_YEONSU_GO_KR_82DAD1CC",
        "url": "https://www.yeonsu.go.kr/culture/edu/culture/reservation.asp",
        "owner": "MUNI_WWW_YEONSU_GO_KR_82DAD1CC",
    },
    "MUNI_IR_D48F81444F9F": {
        "decision": "separate_performance_owner_wrong_category",
        "provider": "MUNI_WWW_YEONSU_GO_KR_B2B6DF58",
        "url": "https://www.yeonsu.go.kr/culture/show/friday_art/reservation.asp",
        "owner": "MUNI_WWW_YEONSU_GO_KR_B2B6DF58",
    },
    "MUNI_IR_6FC6F8469CA1": {
        "decision": "separate_incheon_city_aggregator_owner",
        "provider": "INCHEON_RESERVATION",
        "url": "https://www.incheon.go.kr/res/",
        "owner": "INCHEON_RESERVATION",
    },
    "MUNI_IR_D9D66AAF7469": {
        "decision": "separate_district_library_platform_owner",
        "provider": "MUNI_WWW_YSPUBLICLIB_GO_KR_E04866DC",
        "url": "https://www.yspubliclib.go.kr",
        "owner": "MUNI_WWW_YSPUBLICLIB_GO_KR_E04866DC",
    },
}

YEONSU_EXCLUDED_SOURCE_AUDIT: tuple[Mapping[str, str], ...] = (
    {
        "url": "https://www.yeonsu.go.kr/culture/edu/lecture/reservation.asp?edu_kind=sports",
        "reason": "culture_portal_marine_sports_partition_separate_owner",
    },
    {
        "url": "https://www.yeonsu.go.kr/culture/",
        "reason": "navigation_shell_not_course_ledger",
    },
    {
        "url": "https://www.yspubliclib.go.kr",
        "reason": "district_libraries_have_a_separate_programme_platform",
    },
    {
        "url": "https://lib.ice.go.kr/yeonsu",
        "reason": "education_office_library_not_district_owner",
    },
    {
        "url": "https://www.incheon.go.kr/res/",
        "reason": "metropolitan_aggregator_not_resident_centre_republication",
    },
)

# The live database contains five exact rows whose detail page has a reversed
# full-year education interval.  The ID, list/detail title and branch are bound
# so a reused identity or corrected record cannot be silently discarded.
YEONSU_AUDITED_REVERSED_PERIODS: Mapping[str, Mapping[str, str]] = {
    "42584": {
        "list_title": "민요장구와사물놀이",
        "detail_title": "민요장구와사물놀이(성인)",
        "start": "2026-10-05",
        "end": "2026-03-31",
        "branch": "동춘2동 주민자치센터",
    },
    "43749": {
        "list_title": "파워이브닝요가",
        "detail_title": "파워이브닝요가(성인)",
        "start": "2026-04-02",
        "end": "2026-03-31",
        "branch": "옥련1동 주민자치센터",
    },
    "43742": {
        "list_title": "사교댄스(중급)",
        "detail_title": "사교댄스(중급)(성인)",
        "start": "2026-04-01",
        "end": "2026-03-31",
        "branch": "옥련1동 주민자치센터",
    },
    "43738": {
        "list_title": "색소폰(B)",
        "detail_title": "색소폰(B)(성인)",
        "start": "2026-04-01",
        "end": "2026-03-31",
        "branch": "옥련1동 주민자치센터",
    },
    "43737": {
        "list_title": "색소폰(A)",
        "detail_title": "색소폰(A)(성인)",
        "start": "2026-04-07",
        "end": "2026-03-31",
        "branch": "옥련1동 주민자치센터",
    },
}

YEONSU_DISCOVERY_AUDIT: Mapping[str, Any] = {
    "checked_on": "2026-07-22",
    "unfiltered_archive_pages": 1813,
    "unfiltered_archive_rows": 21754,
    "unfiltered_sentinel_page": 1814,
    "previous_year_partition": "2025-01-01..2025-12-31",
    "previous_year_source_rows": 2208,
    "previous_year_data_pages": 184,
    "previous_year_current_rows": 0,
    "current_future_partition": "2026-01-01..2099-12-31",
    "current_future_source_rows": 1749,
    "current_future_data_pages": 146,
    "current_detail_candidates": 585,
    "audited_reversed_period_rows": 5,
    "publishable_current_rows": 580,
    "required_list_requests": 336,
    "required_detail_requests": 585,
    "complete_network_requests": 921,
    "application_rows": 9,
    "application_controls": 18,
    "privacy_violations": 0,
}


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
Sleeper = Callable[[float], None]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]
Parser = Callable[[BeautifulSoup, str], Any]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PARTIAL_DATE_RE = re.compile(
    r"(?<!\d)(?:(20\d{2})\s*[.\-/]\s*)?"
    r"(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)"
)
_PAGE_TITLE_RE = re.compile(r"^([1-9]\d*)\s+page$")
_CAPACITY_RE = re.compile(r"([가-힣]+)\s*\[\s*([\d,]+)\s*/\s*([\d,]+)\s*\]")
_SESSIONS_RE = re.compile(r"^\s*([\d,]+)\s*일\s*$")
_BRANCH_RE = re.compile(
    r"^(옥련[12]|선학|연수[123]|청학|동춘[123]|송도\s*[1-5])동\s*"
    r"주민자치(?:센터|회)$"
)
_SCHEDULE_RE = re.compile(r"\((.*)\)\s*$")

_LIST_TITLE = "인천광역시 연수구 주민자치센터 프로그램 온라인 신청"
_LIST_LABELS = ("신청기간", "교육기간", "수강료", "교육기관")
_DETAIL_SAFE_LABELS = frozenset(
    {
        "교육대상",
        "기수",
        "수강료",
        "신청현황",
        "신청기간",
        "교육기간",
        "교육기관",
        "교육장소",
        "총수강일수",
    }
)
_DETAIL_REQUIRED_LABELS = _DETAIL_SAFE_LABELS
_DETAIL_NONEMPTY_LABELS = frozenset({"신청기간", "교육기간", "교육기관"})
_PRIVATE_DETAIL_LABELS = frozenset(
    {
        "서버시간",
        "안내",
        "찾아오시는길",
        "문의전화",
        "강좌소개",
        "강좌사진",
        "강좌소개문서",
        "강사명",
        "입금정보",
        "취소환불규정",
        "인원제한방법",
        "첨부파일",
    }
)
_STATUS_MAP: Mapping[str, str] = {
    "접수가능": "OPEN",
    "접수중": "OPEN",
    "방문접수중": "OPEN",
    "대기접수중": "OPEN",
    "접수예정": "SCHEDULED",
    "신청예정": "SCHEDULED",
    "신청마감": "CLOSED",
    "접수마감": "CLOSED",
    "교육종료": "CLOSED",
}
_LIST_DETAIL_STATUS_EQUIVALENCE = {
    ("접수가능", "접수중"),
    ("접수중", "접수중"),
    ("방문접수중", "방문접수중"),
    ("대기접수중", "대기접수중"),
    # The list can retain its wait-list badge after the identity-bound detail
    # has closed.  The detail has the capacity and application controls, so it
    # is authoritative for this exact forward transition.
    ("대기접수중", "신청마감"),
    ("접수예정", "접수예정"),
    ("신청예정", "신청예정"),
    ("신청마감", "신청마감"),
    ("접수마감", "접수마감"),
    ("교육종료", "교육종료"),
}
_PARTNER_BRANCHES = frozenset(
    {
        "청학동 안골창작플랫폼",
        "청학동 청능마을",
        "상생교류센터 2층 프로그램1실",
        "연수구청 교육지원과",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value)).casefold()


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise YeonsuContractError(
            f"expected one {label}, found {len(found)}"
        )
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
        raise YeonsuContractError(f"{label} may not be boolean")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise YeonsuContractError(f"invalid {label}") from exc
    if result < 1:
        raise YeonsuContractError(f"{label} must be positive")
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
    return (
        f"https://{parsed.hostname.rstrip('.').lower()}{_normal_path(parsed.path)}"
        + (f"?{query}" if query else "")
    )


def is_yeonsu_education_target(target: Any) -> bool:
    if _clean(_target_value(target, "provider")) != YEONSU_PROVIDER:
        return False
    compared = _compare_url(_target_value(target, "url"))
    return compared in {
        _compare_url(YEONSU_CANONICAL_URL),
        _compare_url(YEONSU_LEGACY_DETAIL_URL),
    }


is_target = is_yeonsu_education_target


def yeonsu_partitions(cutoff: date) -> tuple[YeonsuPartition, YeonsuPartition]:
    if cutoff.year < 2001 or cutoff.year >= YEONSU_FUTURE_END.year:
        raise YeonsuContractError("unsupported crawl year")
    previous_year = cutoff.year - 1
    return (
        YeonsuPartition(
            "previous_year_rollover",
            date(previous_year, 1, 1),
            date(previous_year, 12, 31),
        ),
        YeonsuPartition(
            "current_future_starts",
            date(cutoff.year, 1, 1),
            YEONSU_FUTURE_END,
        ),
    )


def yeonsu_list_url(partition: YeonsuPartition, page: int = 1) -> str:
    current_page = _positive_int(page, "list page")
    return YEONSU_CANONICAL_URL + "?" + urlencode(
        (
            ("gotopage", current_page),
            ("edu_kind", ""),
            ("team_idx", ""),
            ("strMode", ""),
            ("strCode", "0"),
            ("strKind", ""),
            ("strSearch", "ok"),
            ("strSearch01", "lec_name2"),
            ("strSearch02", ""),
            ("s_target", ""),
            ("s_date", partition.start.isoformat()),
            ("e_date", partition.end.isoformat()),
        )
    )


def yeonsu_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        raise YeonsuContractError("invalid Yeonsu course identity")
    return YEONSU_CANONICAL_URL + "?" + urlencode(
        (("page", "v"), ("lec_idx", value))
    )


def _single_query(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    return _clean(values[0]) if len(values) == 1 else ""


def canonical_yeonsu_detail_identity(current_url: str, value: Any) -> str:
    parsed = urlparse(urljoin(current_url, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = {
        "page",
        "lec_idx",
        "gotopage",
        "dept_idx",
        "list_url",
        "edu_kind",
        "team_idx",
        "strMode",
        "strCode",
        "strKind",
        "strSearch",
        "strSearch01",
        "strSearch02",
        "s_target",
        "s_date",
        "e_date",
    }
    identity = _single_query(query, "lec_idx")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != YEONSU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or _normal_path(parsed.path) != YEONSU_LIST_PATH
        or parsed.params
        or parsed.fragment
        or not set(query).issubset(allowed)
        or _single_query(query, "page") != "v"
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return ""
    return identity


def _partial_dates(value: Any, label: str) -> list[tuple[int, int, int]]:
    found: list[tuple[int, int, int]] = []
    for raw_year, raw_month, raw_day in _PARTIAL_DATE_RE.findall(_clean(value)):
        year = int(raw_year) if raw_year else 0
        month = int(raw_month)
        day = int(raw_day)
        try:
            date(year or 2000, month, day)
        except ValueError as exc:
            raise YeonsuContractError(f"{label} contains invalid date") from exc
        found.append((year, month, day))
    if len(found) != 2:
        raise YeonsuContractError(f"{label} is not an exact date range")
    return found


def _partition_education_range(
    value: Any, partition: YeonsuPartition
) -> tuple[str, str, str]:
    found = _partial_dates(value, "list education period")
    start_year = found[0][0] or partition.start.year
    if not (partition.start.year <= start_year <= partition.end.year):
        raise YeonsuContractError("list education year left requested partition")
    end_year = found[1][0] or start_year + (found[1][1] < found[0][1])
    try:
        start = date(start_year, found[0][1], found[0][2])
        end = date(end_year, found[1][1], found[1][2])
    except ValueError as exc:
        raise YeonsuContractError("list education period is invalid") from exc
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _list_application_range(value: Any) -> tuple[str, str, str, bool]:
    found = _partial_dates(value, "list application period")
    start_year = found[0][0] or found[1][0]
    if not start_year:
        raise YeonsuContractError("list application period has no anchor year")
    end_year = found[1][0] or start_year + (found[1][1] < found[0][1])
    try:
        start = date(start_year, found[0][1], found[0][2])
        end = date(end_year, found[1][1], found[1][2])
    except ValueError as exc:
        raise YeonsuContractError("list application period is invalid") from exc
    valid = end >= start
    return (
        start.isoformat(),
        end.isoformat(),
        f"{start.isoformat()} ~ {end.isoformat()}",
        valid,
    )


def _month_day_signature(value: Any, label: str) -> tuple[tuple[int, int], ...]:
    full = _FULL_DATE_RE.findall(_clean(value))
    if len(full) >= 2:
        if len(full) != 2:
            raise YeonsuContractError(f"{label} is not an exact full-year range")
        result: list[tuple[int, int]] = []
        for raw_year, raw_month, raw_day in full:
            try:
                parsed = date(int(raw_year), int(raw_month), int(raw_day))
            except ValueError as exc:
                raise YeonsuContractError(f"{label} contains invalid date") from exc
            result.append((parsed.month, parsed.day))
        return tuple(result)
    return tuple((month, day) for _, month, day in _partial_dates(value, label))


def _page_from_href(
    href: Any, partition: YeonsuPartition
) -> int:
    parsed = urlparse(urljoin(YEONSU_CANONICAL_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    expected = {
        "edu_kind": "",
        "team_idx": "",
        "strMode": "",
        "strCode": "0",
        "strKind": "",
        "strSearch": "ok",
        "strSearch01": "lec_name2",
        "strSearch02": "",
        "s_target": "",
        "s_date": partition.start.isoformat(),
        "e_date": partition.end.isoformat(),
    }
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != YEONSU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != YEONSU_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != {"gotopage", *expected}
        or any(_single_query(query, key) != value for key, value in expected.items())
    ):
        return 0
    raw = _single_query(query, "gotopage")
    return int(raw) if _IDENTITY_RE.fullmatch(raw) else 0


def _list_contract(
    soup: BeautifulSoup,
    *,
    partition: YeonsuPartition,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[int, Tag]:
    if _text(_one(soup.select("title"), "list title")) != _LIST_TITLE:
        raise YeonsuContractError("list title changed")
    form = _one(soup.select("form[name='frm_dong']"), "list search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != YEONSU_LIST_PATH
    ):
        raise YeonsuContractError("list search form changed")
    expected_fields = {
        "strSearch01": "lec_name2",
        "strSearch": "ok",
        "s_date": partition.start.isoformat(),
        "e_date": partition.end.isoformat(),
        "strSearch02": "",
    }
    for name, expected in expected_fields.items():
        field = _one(form.select(f"input[name='{name}']"), f"list {name} field")
        if _clean(field.get("value")) != expected:
            raise YeonsuContractError(f"list {name} filter changed")

    pager = _one(soup.select("div.paging"), "list pagination")
    pages: list[int] = []
    for anchor in pager.select("a[href]"):
        title = _clean(anchor.get("title"))
        if title and not _PAGE_TITLE_RE.fullmatch(title):
            continue
        linked_page = _page_from_href(anchor.get("href"), partition)
        if linked_page:
            pages.append(linked_page)
    if not pages:
        raise YeonsuContractError("pagination exposes no safe page links")
    last_links = pager.select("a.last[href]")
    if expected_last is None:
        if len(last_links) == 1:
            last = _page_from_href(last_links[0].get("href"), partition)
        elif not last_links:
            last = max(pages)
        else:
            last = 0
        if last < 1:
            raise YeonsuContractError("cannot derive the last list page")
    else:
        last = expected_last
        if any(linked < 1 or linked > last for linked in pages):
            raise YeonsuContractError("pagination escaped the advertised range")
        if last_links and (
            len(last_links) != 1
            or _page_from_href(last_links[0].get("href"), partition) != last
        ):
            raise YeonsuContractError("last-page control changed")

    root = _one(soup.select("div.donglec_list"), "course list")
    active = pager.select("a.select[title]")
    if page <= last:
        selected = _one(active, "active page")
        match = _PAGE_TITLE_RE.fullmatch(_clean(selected.get("title")))
        if match is None or int(match.group(1)) != page:
            raise YeonsuContractError("active page differs from request")
    elif page == last + 1:
        if active:
            raise YeonsuContractError("sentinel page has an active page")
    else:
        raise YeonsuContractError("request passed the immediate sentinel")
    return last, root


def _parse_list_page(
    soup: BeautifulSoup,
    *,
    partition: YeonsuPartition,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _list_contract(
        soup, partition=partition, page=page, expected_last=expected_last
    )
    items = root.select(":scope > ul > li")
    if page < last and len(items) != YEONSU_PAGE_SIZE:
        raise YeonsuContractError(f"page {page} is short before the boundary")
    if page == last and not (1 <= len(items) <= YEONSU_PAGE_SIZE):
        raise YeonsuContractError("last data page has an invalid row count")
    if page == last + 1 and items:
        raise YeonsuContractError("post-boundary sentinel is not empty")

    rows: list[dict[str, Any]] = []
    current_url = yeonsu_list_url(partition, page)
    for position, item in enumerate(items, 1):
        link = _one(item.select(":scope > a[href]"), "course detail link")
        identity = canonical_yeonsu_detail_identity(current_url, link.get("href"))
        title = _text(_one(link.select("dt > p"), "course title"))
        if not identity or not title:
            raise YeonsuContractError(f"page {page} row {position} identity/title changed")
        values: dict[str, str] = {}
        source_status = ""
        labels: list[str] = []
        for entry in link.select("dd > ul > li"):
            key = _text(_one(entry.select(":scope > .q"), "course label"))
            value_node = _one(entry.select(":scope > .a"), "course value")
            status_nodes = value_node.select(":scope > .lec_state")
            value = _text(value_node)
            if status_nodes:
                status_node = _one(status_nodes, "course status")
                source_status = _text(status_node)
                value = _clean(value.replace(source_status, "", 1))
            values[key] = _clean(value.lstrip(":"))
            labels.append(key)
        if tuple(labels) != _LIST_LABELS or any(not values[key] for key in _LIST_LABELS):
            raise YeonsuContractError(f"page {page} row {position} labels changed")
        if source_status not in _STATUS_MAP:
            raise YeonsuContractError(f"unknown list status {source_status!r}")
        apply_start, apply_end, apply_period, application_period_valid = _list_application_range(
            values["신청기간"]
        )
        start, end, period = _partition_education_range(
            values["교육기간"], partition
        )
        branch = values["교육기관"]
        row: dict[str, Any] = {
                "provider": YEONSU_PROVIDER,
                "provider_course_id": f"{YEONSU_PROVIDER}:resident:{identity}",
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "program_type": "교육·강좌",
                "category": "주민자치센터 프로그램",
                "branch": branch,
                "branch_code": _branch_code(branch),
                "branch_url": YEONSU_CANONICAL_URL,
                "preserve_branch": True,
                "raw_url": yeonsu_detail_url(identity),
                "status": _STATUS_MAP[source_status],
                "period": period,
                "start_date": start,
                "end_date": end,
                "fee": values["수강료"],
                "reservation_available": False,
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "municipality_code": YEONSU_MUNICIPALITY_CODE,
                "municipality_full_name": YEONSU_MUNICIPALITY_NAME,
                "collection_type": YEONSU_PARSER,
                "raw_fields": {
                    "parser": YEONSU_PARSER,
                    "source_catalog": partition.key,
                    "source_identity": identity,
                    "source_page": page,
                    "source_status": source_status,
                    "list_application_period": values["신청기간"],
                    "list_education_period": values["교육기간"],
                    "list_fee": values["수강료"],
                    "list_branch": branch,
                    "application_period_valid": application_period_valid,
                },
            }
        if application_period_valid:
            row.update(
                {
                    "apply_period": apply_period,
                    "apply_start": apply_start,
                    "apply_end": apply_end,
                }
            )
        else:
            row["raw_fields"].update(
                {
                    "invalid_apply_start": apply_start,
                    "invalid_apply_end": apply_end,
                }
            )
        rows.append(row)
    return rows, last


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(
        f"{YEONSU_PROVIDER}|{_compact(branch)}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"YEONSU_BRANCH_{digest}"


def _canonical_branch(value: Any) -> str:
    branch = _clean(value)
    match = _BRANCH_RE.fullmatch(branch)
    if match:
        stem = re.sub(r"\s+", "", match.group(1))
        return f"{stem}동 주민자치센터"
    if branch in _PARTNER_BRANCHES:
        return branch
    raise YeonsuContractError(f"current detail branch left Yeonsu ownership: {branch}")


def _full_date_range(value: Any, label: str) -> tuple[date, date]:
    found: list[date] = []
    for raw_year, raw_month, raw_day in _FULL_DATE_RE.findall(_clean(value)):
        try:
            found.append(date(int(raw_year), int(raw_month), int(raw_day)))
        except ValueError as exc:
            raise YeonsuContractError(f"{label} contains invalid date") from exc
    if len(found) != 2:
        raise YeonsuContractError(f"{label} is not an exact full-year range")
    return found[0], found[1]


def _capacity(value: Any) -> tuple[int, int, int, int]:
    matches = _CAPACITY_RE.findall(_clean(value))
    if not matches:
        raise YeonsuContractError("detail capacity changed")
    current = total = wait_current = wait_total = 0
    seen: set[str] = set()
    for label, raw_current, raw_total in matches:
        if label in seen or label not in {"인터넷", "방문", "대기"}:
            raise YeonsuContractError("detail capacity channel changed")
        seen.add(label)
        used = int(raw_current.replace(",", ""))
        limit = int(raw_total.replace(",", ""))
        if used < 0 or limit < 0:
            raise YeonsuContractError("detail capacity is negative")
        if label == "대기":
            wait_current += used
            wait_total += limit
        else:
            current += used
            total += limit
    return current, total, wait_current, wait_total


def _application_url(anchor: Tag, identity: str) -> str:
    parsed = urlparse(urljoin(YEONSU_CANONICAL_URL, _clean(anchor.get("href"))))
    query = parse_qs(parsed.query, keep_blank_values=True)
    required = {"page", "lec_idx", "age_idx", "lec_onlineP", "lec_limitMethod"}
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != YEONSU_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.path != YEONSU_LIST_PATH
        or parsed.params
        or parsed.fragment
        or set(query) != required
        or _single_query(query, "page") != "r"
        or _single_query(query, "lec_idx") != identity
        or any(
            not re.fullmatch(r"\d+", _single_query(query, key))
            for key in ("age_idx", "lec_onlineP", "lec_limitMethod")
        )
    ):
        raise YeonsuContractError("unsafe application control")
    return YEONSU_CANONICAL_URL + "?" + urlencode(
        (
            ("page", "r"),
            ("lec_idx", identity),
            ("age_idx", _single_query(query, "age_idx")),
            ("lec_onlineP", _single_query(query, "lec_onlineP")),
            ("lec_limitMethod", _single_query(query, "lec_limitMethod")),
        )
    )


def _safe_detail_values(root: Tag) -> tuple[dict[str, str], tuple[str, ...]]:
    safe: dict[str, str] = {}
    labels: list[str] = []
    for definition in root.select("dl"):
        heading = definition.find("dt")
        value = definition.find("dd")
        key = _text(heading)
        if not key or value is None:
            continue
        if key in labels:
            raise YeonsuContractError(f"duplicate detail label {key!r}")
        labels.append(key)
        if key in _DETAIL_SAFE_LABELS:
            safe[key] = _text(value)
        # Every non-allowlisted value is intentionally left unread.  Optional
        # private/free-form labels vary by course (for example 강사이력), so
        # their appearance is evidence for redaction rather than permission
        # to inspect their value.
    if set(_DETAIL_REQUIRED_LABELS) - set(safe):
        raise YeonsuContractError("required safe detail fields are incomplete")
    empty = sorted(key for key in _DETAIL_NONEMPTY_LABELS if not safe[key])
    if empty:
        raise YeonsuContractError(
            "required safe detail value is empty: " + ", ".join(empty)
        )
    return safe, tuple(labels)


def _detail_title(root: Tag, source_status: str) -> str:
    heading = _one(root.select(":scope > .board_title"), "detail title")
    status_node = _one(heading.select(":scope > .state"), "detail status")
    status = _text(status_node)
    if status != source_status:
        raise YeonsuContractError("detail status changed while reading title")
    clone = BeautifulSoup(str(heading), "lxml")
    for node in clone.select(".state"):
        node.extract()
    return _clean(clone.get_text(" ", strip=True))


def _title_matches(list_title: Any, detail_title: Any) -> bool:
    listed = _compact(list_title)
    detailed = _compact(detail_title)
    if listed == detailed:
        return True
    suffix = detailed[len(listed) :] if detailed.startswith(listed) else ""
    return bool(suffix.startswith("(") and suffix.endswith(")"))


def _parse_detail(
    soup: BeautifulSoup,
    final_url: str,
    parent: Mapping[str, Any],
    cutoff: date,
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    identity = _clean(parent.get("raw_fields", {}).get("source_identity"))
    if _compare_url(final_url) != _compare_url(yeonsu_detail_url(identity)):
        raise YeonsuContractError("detail response URL changed")
    if _text(_one(soup.select("title"), "detail page title")) != _LIST_TITLE:
        raise YeonsuContractError("detail page title changed")
    root = _one(soup.select("div.board_view"), "detail board")
    source_status = _text(
        _one(root.select(":scope > .board_title > .state"), "detail status")
    )
    if source_status not in _STATUS_MAP:
        raise YeonsuContractError("unknown detail status")
    list_status = _clean(parent.get("raw_fields", {}).get("source_status"))
    if (list_status, source_status) not in _LIST_DETAIL_STATUS_EQUIVALENCE:
        raise YeonsuContractError(
            f"{identity}: list/detail status mismatch "
            f"({list_status!r} -> {source_status!r})"
        )
    status_mismatch = list_status != source_status
    detail_title = _detail_title(root, source_status)
    if not _title_matches(parent.get("title"), detail_title):
        raise YeonsuContractError(f"{identity}: list/detail title mismatch")
    safe, labels = _safe_detail_values(root)
    if _month_day_signature(
        parent.get("raw_fields", {}).get("list_application_period"),
        "list application period",
    ) != _month_day_signature(safe["신청기간"], "detail application period"):
        raise YeonsuContractError(f"{identity}: application dates mismatch")
    if _month_day_signature(
        parent.get("raw_fields", {}).get("list_education_period"),
        "list education period",
    ) != _month_day_signature(safe["교육기간"], "detail education period"):
        raise YeonsuContractError(f"{identity}: education dates mismatch")
    if _compact(safe["교육기관"]) != _compact(
        parent.get("raw_fields", {}).get("list_branch")
    ):
        raise YeonsuContractError(f"{identity}: list/detail branch mismatch")
    if safe["수강료"] and not _compact(safe["수강료"]).startswith(
        _compact(parent.get("raw_fields", {}).get("list_fee"))
    ):
        raise YeonsuContractError(f"{identity}: list/detail fee mismatch")

    apply_start, apply_end = _full_date_range(
        safe["신청기간"], "detail application period"
    )
    start, end = _full_date_range(safe["교육기간"], "detail education period")
    branch = _canonical_branch(safe["교육기관"])
    anomaly = YEONSU_AUDITED_REVERSED_PERIODS.get(identity)
    if anomaly is not None:
        expected = (
            _compact(anomaly["list_title"]),
            _compact(anomaly["detail_title"]),
            anomaly["start"],
            anomaly["end"],
            _compact(anomaly["branch"]),
        )
        observed = (
            _compact(parent.get("title")),
            _compact(detail_title),
            start.isoformat(),
            end.isoformat(),
            _compact(safe["교육기관"]),
        )
        if observed != expected or end >= start:
            raise YeonsuContractError(
                f"{identity}: audited reversed-period binding changed"
            )
        return None, {
            "identity": identity,
            "title": _clean(parent.get("title")),
            "reason": "audited_reversed_education_period",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "would_be_current_from_list": bool(
                date.fromisoformat(_clean(parent.get("end_date"))) >= cutoff
            ),
        }
    if apply_end < apply_start:
        raise YeonsuContractError(f"{identity}: detail application period is reversed")
    if end < start:
        raise YeonsuContractError(f"{identity}: unreviewed reversed education period")
    if end < cutoff:
        raise YeonsuContractError(f"{identity}: current candidate ended before cutoff")

    controls = soup.select("a.btn.btn_ok[href]")
    application_url = ""
    if source_status == "접수중":
        if len(controls) != 2:
            raise YeonsuContractError(f"{identity}: online control count changed")
        control_urls = {_application_url(control, identity) for control in controls}
        if len(control_urls) != 1:
            raise YeonsuContractError(f"{identity}: online controls disagree")
        application_url = control_urls.pop()
    elif controls:
        raise YeonsuContractError(f"{identity}: non-online row gained an application control")

    capacity_values: Optional[tuple[int, int, int, int]] = None
    if safe["신청현황"]:
        capacity_values = _capacity(safe["신청현황"])
    sessions_match = (
        _SESSIONS_RE.fullmatch(safe["총수강일수"])
        if safe["총수강일수"]
        else None
    )
    if safe["총수강일수"] and sessions_match is None:
        raise YeonsuContractError(f"{identity}: session count changed")
    schedule_match = _SCHEDULE_RE.search(safe["교육기간"])
    application_type = "INFO_ONLY"
    method = ""
    if source_status == "접수중":
        application_type, method = "ONLINE_RESERVATION", "온라인"
    elif source_status == "방문접수중":
        application_type, method = "OFFLINE_RESERVATION", "방문"
    elif source_status == "대기접수중":
        application_type, method = "WAITLIST_INFO_ONLY", "대기"

    row = dict(parent)
    row.update(
        {
            "branch": branch,
            "branch_code": _branch_code(branch),
            "status": _STATUS_MAP[source_status],
            "period": f"{start.isoformat()} ~ {end.isoformat()}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
            "apply_start": apply_start.isoformat(),
            "apply_end": apply_end.isoformat(),
            "schedule_raw": _clean(schedule_match.group(1)) if schedule_match else "",
            "target": safe["교육대상"],
            "fee": safe["수강료"] or parent.get("fee", ""),
            "room": safe["교육장소"],
            "venue_name": safe["교육기관"],
            "application_type": application_type,
            "application_method_raw": method,
            "reservation_available": bool(application_url),
        }
    )
    if capacity_values is not None:
        capacity_current, capacity_total, wait_current, wait_total = capacity_values
        row.update(
            {
                "capacity": safe["신청현황"],
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "waitlist_current": wait_current,
                "waitlist_total": wait_total,
            }
        )
    if sessions_match is not None:
        row["sessions"] = int(sessions_match.group(1).replace(",", ""))
    if application_url:
        row["application_url"] = application_url
    raw = dict(parent.get("raw_fields", {}))
    raw.update(
        {
            "detail_verified": True,
            "detail_source_status": source_status,
            "list_detail_status_mismatch": status_mismatch,
            "detail_application_control": bool(application_url),
            "detail_safe_labels": tuple(sorted(_DETAIL_SAFE_LABELS)),
            "contact_value_never_read": "문의전화" in labels,
            "instructor_value_never_read": "강사명" in labels,
            "description_value_never_read": "강좌소개" in labels,
            "attachment_values_never_read": any(
                label in labels for label in ("강좌사진", "강좌소개문서", "첨부파일")
            ),
            "payment_refund_values_never_read": any(
                label in labels for label in ("입금정보", "취소환불규정")
            ),
            "application_form_fetched": False,
            "applicant_list_fetched": False,
        }
    )
    row["raw_fields"] = raw
    return _clean_row(row), None


def _list_signature(rows: Sequence[Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            _clean(row.get("raw_fields", {}).get("source_identity")),
            _clean(row.get("title")),
            _clean(row.get("raw_fields", {}).get("source_status")),
            _clean(row.get("raw_fields", {}).get("list_application_period")),
            _clean(row.get("raw_fields", {}).get("list_education_period")),
            _clean(row.get("raw_fields", {}).get("list_branch")),
        )
        for row in rows
    )


def _clean_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if value not in (None, "", [], {}, ())
    }


def _default_dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
            ),
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
                raise YeonsuContractError("network request cap reached")
            self.requests += 1
            if retry:
                self.retries += 1

    def get(self, url: str, parser: Parser) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in range(YEONSU_FETCH_ATTEMPTS):
            self._consume(attempt > 0)
            try:
                response = self.fetcher(self._session(), url, self.timeout)
                if isinstance(response, BeautifulSoup):
                    soup, final_url = response, url
                elif (
                    isinstance(response, tuple)
                    and len(response) == 2
                    and isinstance(response[0], BeautifulSoup)
                ):
                    soup, final_url = response
                else:
                    status = int(getattr(response, "status_code", 200) or 0)
                    final_url = _clean(getattr(response, "url", url)) or url
                    content = bytes(getattr(response, "content", b"") or b"")
                    if status != 200:
                        raise RuntimeError(f"HTTP {status}")
                    if not content:
                        text = getattr(response, "text", "")
                        content = str(text or "").encode("utf-8")
                    if not content:
                        raise RuntimeError("empty HTML response")
                    if len(content) > YEONSU_MAX_HTML_BYTES:
                        raise YeonsuContractError("HTML response is too large")
                    soup = BeautifulSoup(content, "lxml")
                if _compare_url(final_url) != _compare_url(url):
                    raise YeonsuContractError("request response URL changed")
                return parser(soup, final_url)
            except YeonsuContractError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < YEONSU_FETCH_ATTEMPTS:
                    self.sleeper(0.35 * (attempt + 1))
        raise YeonsuContractError(
            f"failed source fetch after retries: {last_error}"
        ) from last_error

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
        futures = {
            executor.submit(runner.get, url, parser): key
            for key, url, parser in jobs
        }
        for future in as_completed(futures):
            key = futures[future]
            result[key] = future.result()
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
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "returned_count": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "candidate_id": YEONSU_CANONICAL_CANDIDATE_ID,
        "canonical_url": YEONSU_CANONICAL_URL,
        "ownership_scope": YEONSU_OWNERSHIP_SCOPE,
        "owner_boundary_audit": dict(YEONSU_OWNER_BOUNDARY_AUDIT),
        "excluded_source_audit": tuple(YEONSU_EXCLUDED_SOURCE_AUDIT),
        "discovery_audit": dict(YEONSU_DISCOVERY_AUDIT),
    }


def _collect_partition(
    runner: _Runner,
    partition: YeonsuPartition,
    *,
    max_pages: int,
    max_workers: int,
) -> tuple[list[dict[str, Any]], int]:
    first_rows, last = runner.get(
        yeonsu_list_url(partition, 1),
        lambda soup, _final: _parse_list_page(
            soup, partition=partition, page=1
        ),
    )
    if last + 1 > max_pages:
        raise YeonsuContractError(
            f"{partition.key} pages exceed max_pages cap"
        )
    pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
    jobs: list[tuple[int, str, Parser]] = []
    for page in range(2, last + 1):
        jobs.append(
            (
                page,
                yeonsu_list_url(partition, page),
                lambda soup, _final, page=page: _parse_list_page(
                    soup,
                    partition=partition,
                    page=page,
                    expected_last=last,
                )[0],
            )
        )
    pages.update(_parallel_fetch(runner, jobs, max_workers=max_workers))
    sentinel_rows, _ = runner.get(
        yeonsu_list_url(partition, last + 1),
        lambda soup, _final: _parse_list_page(
            soup,
            partition=partition,
            page=last + 1,
            expected_last=last,
        ),
    )
    if sentinel_rows:
        raise YeonsuContractError(f"{partition.key} sentinel is not empty")
    rechecks = _parallel_fetch(
        runner,
        (
            (
                "first",
                yeonsu_list_url(partition, 1),
                lambda soup, _final: _parse_list_page(
                    soup,
                    partition=partition,
                    page=1,
                    expected_last=last,
                )[0],
            ),
            (
                "last",
                yeonsu_list_url(partition, last),
                lambda soup, _final: _parse_list_page(
                    soup,
                    partition=partition,
                    page=last,
                    expected_last=last,
                )[0],
            ),
        ),
        max_workers=min(2, max_workers),
    )
    if (
        _list_signature(rechecks["first"]) != _list_signature(pages[1])
        or _list_signature(rechecks["last"]) != _list_signature(pages[last])
    ):
        raise YeonsuContractError(f"{partition.key} boundary changed during crawl")
    rows = [row for page in range(1, last + 1) for row in pages[page]]
    identities = [
        _clean(row.get("raw_fields", {}).get("source_identity")) for row in rows
    ]
    if len(identities) != len(set(identities)):
        raise YeonsuContractError(f"{partition.key} has duplicate identities")
    return rows, last


def collect_incheon_yeonsu_education(
    target: Any,
    timeout: int = 30,
    max_pages: int = 220,
    detail_limit: int = 700,
    max_requests: int = 1200,
    *,
    today: Optional[date | datetime | str] = None,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    sleeper: Sleeper = time.sleep,
    max_workers: int = YEONSU_MAX_WORKERS,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Return one complete, fail-closed current/future Yeonsu snapshot."""

    meta = _base_meta()
    if not is_yeonsu_education_target(target):
        meta["configured_collection_error"] = (
            "target does not match the canonical/registered Yeonsu owner"
        )
        return [], YEONSU_PARSER, meta
    try:
        timeout = _positive_int(timeout, "timeout")
        max_pages = _positive_int(max_pages, "max_pages")
        detail_limit = _positive_int(detail_limit, "detail_limit")
        max_requests = _positive_int(max_requests, "max_requests")
        max_workers = _positive_int(max_workers, "max_workers")
        cutoff = _today(today)
        partitions = yeonsu_partitions(cutoff)
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        return [], YEONSU_PARSER, meta

    runner = _Runner(
        timeout=timeout,
        maximum=max_requests,
        fetcher=fetcher or _default_fetcher,
        session_factory=session_factory or _default_session_factory,
        sleeper=sleeper,
    )
    try:
        partition_rows: dict[str, list[dict[str, Any]]] = {}
        partition_pages: dict[str, int] = {}
        for partition in partitions:
            rows, pages = _collect_partition(
                runner,
                partition,
                max_pages=max_pages,
                max_workers=max_workers,
            )
            partition_rows[partition.key] = rows
            partition_pages[partition.key] = pages
        all_rows = [
            row
            for partition in partitions
            for row in partition_rows[partition.key]
        ]
        identities = [
            _clean(row.get("raw_fields", {}).get("source_identity"))
            for row in all_rows
        ]
        if len(identities) != len(set(identities)):
            raise YeonsuContractError("date partitions overlap by course identity")

        current_rows = [
            row
            for row in all_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        if len(current_rows) > detail_limit:
            meta["source_cap_reached"] = True
            raise YeonsuContractError(
                f"detail_limit cap allows {detail_limit} of "
                f"{len(current_rows)} required details"
            )
        required_list_requests = sum(
            partition_pages[partition.key] + 3 for partition in partitions
        )
        required_requests = required_list_requests + len(current_rows)
        if required_requests > max_requests:
            meta["source_cap_reached"] = True
            raise YeonsuContractError(
                f"max_requests cap allows {max_requests} of {required_requests} "
                "required requests"
            )

        jobs: list[tuple[str, str, Parser]] = []
        for row in current_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            jobs.append(
                (
                    identity,
                    _clean(row.get("raw_url")),
                    lambda soup, final, row=row: _parse_detail(
                        soup, final, row, cutoff
                    ),
                )
            )
        details = _parallel_fetch(runner, jobs, max_workers=max_workers)
        enriched: list[dict[str, Any]] = []
        exclusions: list[dict[str, Any]] = []
        for row in current_rows:
            identity = _clean(row.get("raw_fields", {}).get("source_identity"))
            enriched_row, exclusion = details[identity]
            if enriched_row is not None:
                enriched.append(enriched_row)
            if exclusion is not None:
                exclusions.append(exclusion)
        deduper = dedupe_rows or _default_dedupe
        result = list(deduper(enriched))
        if len(result) != len(enriched):
            raise YeonsuContractError("dedupe changed the atomic row count")
        if len({_clean(row.get("provider_course_id")) for row in result}) != len(result):
            raise YeonsuContractError("returned provider identities are not unique")
        result.sort(
            key=lambda row: int(
                _clean(row.get("raw_fields", {}).get("source_identity"))
            ),
            reverse=True,
        )

        previous_key = partitions[0].key
        current_key = partitions[1].key
        previous_current = sum(
            date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
            for row in partition_rows[previous_key]
        )
        current_partition_current = len(current_rows) - previous_current
        meta.update(
            {
                "pages": sum(partition_pages.values()),
                "list_requests": required_list_requests,
                "required_list_requests": required_list_requests,
                "sentinel_requests": 2,
                "stability_rechecks": 4,
                "detail_attempts": len(current_rows),
                "detail_pages": len(current_rows),
                "detail_errors": 0,
                "source_total": len(all_rows),
                "source_rows": len(all_rows),
                "unique_source_rows": len(all_rows),
                "historical_application_period_defect_count": sum(
                    not bool(
                        row.get("raw_fields", {}).get("application_period_valid")
                    )
                    for row in all_rows
                ),
                "current_source_count": len(current_rows),
                "publishable_current_count": len(result),
                "returned_count": len(result),
                "previous_year_source_rows": len(partition_rows[previous_key]),
                "previous_year_data_pages": partition_pages[previous_key],
                "previous_year_current_count": previous_current,
                "current_future_source_rows": len(partition_rows[current_key]),
                "current_future_data_pages": partition_pages[current_key],
                "current_future_candidate_count": current_partition_current,
                "audited_reversed_period_count": len(exclusions),
                "audited_reversed_period_ids": sorted(
                    (item["identity"] for item in exclusions), key=int
                ),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "source_status_counts": dict(
                    Counter(
                        _clean(row.get("raw_fields", {}).get("source_status"))
                        for row in current_rows
                    )
                ),
                "list_detail_status_mismatch_count": sum(
                    bool(
                        row.get("raw_fields", {}).get(
                            "list_detail_status_mismatch"
                        )
                    )
                    for row in result
                ),
                "list_detail_status_mismatch_ids": [
                    _clean(row.get("raw_fields", {}).get("source_identity"))
                    for row in result
                    if row.get("raw_fields", {}).get(
                        "list_detail_status_mismatch"
                    )
                ],
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "branch_count": len({_clean(row.get("branch")) for row in result}),
                "application_control_count": sum(
                    bool(row.get("application_url")) for row in result
                ),
                "application_anchor_count": 2
                * sum(bool(row.get("application_url")) for row in result),
                "offline_application_count": sum(
                    _clean(row.get("application_type")) == "OFFLINE_RESERVATION"
                    for row in result
                ),
                "waitlist_information_only_count": sum(
                    _clean(row.get("application_type")) == "WAITLIST_INFO_ONLY"
                    for row in result
                ),
                "privacy_violations": 0,
                "pagination_detected": any(page > 1 for page in partition_pages.values()),
                "pagination_complete": True,
                "details_complete": True,
                "snapshot_complete": True,
                "atomic_union_complete": True,
                "source_cap_reached": False,
                "no_current_data": not result,
                "no_current_reason": (
                    "both complete Yeonsu start-date partitions have no "
                    "publishable current/future rows"
                    if not result
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
    except Exception as exc:
        meta["configured_collection_error"] = _clean(exc)
        if "cap" in _clean(exc):
            meta["source_cap_reached"] = True
        return [], YEONSU_PARSER, meta
    finally:
        meta["network_requests"] = runner.requests
        meta["network_retry_count"] = runner.retries
        meta["sessions_created"] = runner.sessions_created
        runner.close()
    return result, YEONSU_PARSER, meta


collect_yeonsu_education = collect_incheon_yeonsu_education
collect_courses = collect_incheon_yeonsu_education
collect = collect_incheon_yeonsu_education


__all__ = [
    "YEONSU_AUDITED_REVERSED_PERIODS",
    "YEONSU_CANONICAL_CANDIDATE_ID",
    "YEONSU_CANONICAL_URL",
    "YEONSU_DISCOVERY_AUDIT",
    "YEONSU_EXCLUDED_SOURCE_AUDIT",
    "YEONSU_LEGACY_DETAIL_CANDIDATE_ID",
    "YEONSU_LEGACY_DETAIL_URL",
    "YEONSU_MUNICIPALITY_CODE",
    "YEONSU_MUNICIPALITY_NAME",
    "YEONSU_OWNER_BOUNDARY_AUDIT",
    "YEONSU_OWNERSHIP_SCOPE",
    "YEONSU_PAGE_SIZE",
    "YEONSU_PARSER",
    "YEONSU_PROVIDER",
    "YEONSU_URL",
    "YeonsuContractError",
    "YeonsuPartition",
    "canonical_yeonsu_detail_identity",
    "collect",
    "collect_courses",
    "collect_incheon_yeonsu_education",
    "collect_yeonsu_education",
    "is_target",
    "is_yeonsu_education_target",
    "yeonsu_detail_url",
    "yeonsu_list_url",
    "yeonsu_partitions",
]
