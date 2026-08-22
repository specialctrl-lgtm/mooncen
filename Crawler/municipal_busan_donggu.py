"""Atomic education collector for Busan Dong-gu's three public ledgers.

The district application exposes five education-bearing menus from one
controller.  Its ``search_Status=T`` value means *all history*, not current
programs.  Native ``LEARNING_*`` rows from Busan Lifelong Learning office
``OFFICE_00002642`` and the exact Dong-gu resident-council partition of
Busan's integrated-reservation service are independent education ledgers.
External platform rows are republications of district ``data_Sid`` records
and are suppressed only after exact identity membership is proved.

Every declared page, immediate post-final sentinel, stability boundary, and
current/future detail is mandatory.  The platform additionally requires two
equal complete semantic censuses.  Any pagination, identity, ownership,
safe-field schema, date, status, or detail mismatch discards the whole union.
Applicant forms/lists, login pages, contact/instructor values, attachments,
and free-form detail values are never requested or persisted.

This module intentionally does not import ``Crawler_MunicipalYaml``.  The
shared router can inject its managed session/fetch/dedupe helpers when the
provider is promoted.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from Crawler import municipal_busan_lifelong as _lifelong


BUSAN_DONGGU_PROVIDER = "BUSAN_DONGGU_RESERVATION"
BUSAN_DONGGU_CANDIDATE_ID = "MUNI_IR_54C60E9E98D9"
BUSAN_DONGGU_URL = "https://www.bsdonggu.go.kr/reserve/index.donggu"
BUSAN_DONGGU_HOST = "www.bsdonggu.go.kr"
BUSAN_DONGGU_PATH = "/reserve/index.donggu"
BUSAN_DONGGU_MUNICIPALITY_CODE = "2617000000"
BUSAN_DONGGU_MUNICIPALITY_NAME = "부산광역시 동구"
BUSAN_LIFELONG_PROVIDER = _lifelong.BUSAN_LIFELONG_PROVIDER
BUSAN_LIFELONG_DONGGU_OFFICE = "OFFICE_00002642"
BUSAN_LIFELONG_DONGGU_OFFICE_NAME = "동구청"
BUSAN_LIFELONG_DONGGU_EXPECTED_OWNERSHIP = "duplicate_dedicated_donggu_owner"
BUSAN_LIFELONG_PAGE_SIZE = 1000
BUSAN_LIFELONG_LIST_URL = _lifelong.BUSAN_LIFELONG_LIST_URL
BUSAN_LIFELONG_LIST_PATH = _lifelong.BUSAN_LIFELONG_LIST_PATH
BUSAN_LIFELONG_DETAIL_PATH = _lifelong.BUSAN_LIFELONG_DETAIL_PATH

BUSAN_CITY_HOST = "reserve.busan.go.kr"
BUSAN_CITY_LIST_PATH = "/lctre/list"
BUSAN_CITY_DETAIL_PATH = "/lctre/view"
BUSAN_CITY_DONGGU_GUGUN = "5"
BUSAN_CITY_RESIDENT_OFFICE = "33"
BUSAN_CITY_DONGGU_URL = (
    f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?"
    + urlencode(
        (
            ("curPage", "1"),
            ("srchGugun", BUSAN_CITY_DONGGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )
)
BUSAN_DONGGU_PARSER = (
    "busan_donggu_integrated_five_categories_complete_pages+sentinels+"
    "stable_first_pages+current_safe_detail+lifelong_office00002642_"
    "pageunit1000_two_complete_censuses+external_datasid_duplicate_"
    "suppression+native_current_safe_detail+busan_reserve_gugun5_office33_"
    "all_pages+sentinel+stable_first_last+current_safe_detail+pii_never_read+"
    "atomic_three_ledger_snapshot"
)
BUSAN_DONGGU_OWNERSHIP_SCOPE = (
    "donggu_complete_five_category_catalogue_native_platform_courses_and_"
    "exact_busan_city_donggu_resident_council_education"
)
BUSAN_DONGGU_MAX_WORKERS = 4
BUSAN_DONGGU_SESSION_REQUEST_LIMIT = 70

Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]


@dataclass(frozen=True)
class DongguCategory:
    code: str
    label: str
    page_size: int
    default_branch: str

    @property
    def root_menu(self) -> str:
        return f"DOM_000000{self.code}000000000"

    @property
    def list_menu(self) -> str:
        return f"DOM_000000{self.code}001000000"

    @property
    def detail_menu(self) -> str:
        return f"DOM_000000{self.code}002000000"

    @property
    def application_menu(self) -> str:
        return f"DOM_000000{self.code}004000000"


BUSAN_DONGGU_CATEGORIES = (
    DongguCategory("701", "평생학습", 10, "부산 동구 평생학습관"),
    DongguCategory("702", "도서관", 10, "부산 동구 도서관 통합교육"),
    DongguCategory("703", "정보화교육", 10, "부산 동구 정보화교육"),
    DongguCategory("706", "어린이영어도서관", 15, "동구어린이영어도서관"),
    DongguCategory("708", "일반", 10, "부산 동구 일반교육"),
)
_CATEGORY_BY_CODE = {item.code: item for item in BUSAN_DONGGU_CATEGORIES}

_SPACE_RE = re.compile(r"\s+")
_COMPACT_DATE_RANGE_RE = re.compile(
    r"^\s*(\d{4})(\d{2})(\d{2})\s*[~～]\s*"
    r"(\d{4})(\d{2})(\d{2})\s*$"
)
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*"
    r"(\d{1,2})(?:\s*일)?(?!\d)"
)
_REG_PROC_RE = re.compile(
    r"fnRegProc\(\s*['\"](\d+)['\"]\s*,.*?,\s*(true|false)\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_IDENTITY_RE = re.compile(r"\d+")
_FRACTION_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_LEARNING_ID_RE = re.compile(r"LEARNING_[0-9]{8}")
_CITY_ACTION_RE = re.compile(
    r"fn_viewProgrm\(\s*['\"]([0-9]+)['\"]\s*,\s*['\"]([0-9]+)['\"]\s*\);\s*"
    r"return\s+false;?"
)
_CITY_DATES_RE = re.compile(
    r"^\[신청\]\s*(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})\s*"
    r"\[행사\]\s*(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})$"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[-\s]?)?(?:0\d{1,3}[-\s]?)?"
    r"\d{3,4}[-\s]\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")

_STATUS_MAP: Mapping[str, str] = {
    "수강신청중": "OPEN",
    "수강신청중(대기자)": "OPEN",
    "접수대기": "SCHEDULED",
    "인원마감": "CLOSED",
    "기간마감": "CLOSED",
}

_DISTRICT_DETAIL_SAFE_LABELS = frozenset(
    {
        "강좌명",
        "접수명",
        "교육시작일",
        "운영시작일",
        "교육종료일",
        "운영종료일",
        "교육시간",
        "운영시간",
        "교육대상",
        "운영대상",
        "접수시작일",
        "접수종료일",
        "신청가능인원",
        "대기가능인원",
        "교육장소",
        "장소",
        "기타경비",
        "교육장소주소",
    }
)
_DISTRICT_DETAIL_SKIPPED_LABELS = frozenset(
    {
        "강좌내용",
        "접수내용",
        "교육문의전화",
        "문의전화",
        "강사명",
        "교육장소위치지정",
    }
)

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
        "강좌분류",
        "교육대상",
        "교육장소",
        "총 교육시간",
        "교육기간",
        "교육시간",
        "수강료",
        "재료비",
        "수강료 기타",
        "우선모집기간",
        "일반모집기간",
        "모집방법",
        "신청상태",
        "교육상태",
        "결제방법",
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
_CITY_DETAIL_SAFE_LABELS = frozenset(_CITY_DETAIL_REQUIRED) - {"문의전화"}

_PAGING_ALLOWED_QUERY = {
    "menuCd",
    "edu_Start_Date_From",
    "edu_Start_Date_To",
    "accept_Start_Date_From",
    "accept_Start_Date_To",
    "data_Title",
    "search_Status",
    "page_no",
    "gubun_l",
}
_DETAIL_ALLOWED_QUERY = _PAGING_ALLOWED_QUERY | {"data_Sid"}


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def is_busan_donggu_target(target: Any) -> bool:
    """Accept only the canonical provider-owned root URL."""

    if _clean(_target_value(target, "provider")) != BUSAN_DONGGU_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname == BUSAN_DONGGU_HOST
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == BUSAN_DONGGU_PATH
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


is_target = is_busan_donggu_target


def busan_donggu_list_url(category: DongguCategory | str, page_no: int = 1) -> str:
    item = _CATEGORY_BY_CODE[category] if isinstance(category, str) else category
    query = urlencode(
        (
            ("menuCd", item.list_menu),
            ("search_Status", "T"),
            ("data_Title", ""),
            ("page_no", str(max(1, int(page_no)))),
            ("gubun_l", ""),
        )
    )
    return f"{BUSAN_DONGGU_URL}?{query}"


def busan_donggu_detail_url(category: DongguCategory | str, identity: str) -> str:
    item = _CATEGORY_BY_CODE[category] if isinstance(category, str) else category
    sid = _clean(identity)
    if not _IDENTITY_RE.fullmatch(sid):
        return ""
    return f"{BUSAN_DONGGU_URL}?{urlencode((('menuCd', item.detail_menu), ('data_Sid', sid)))}"


def busan_donggu_application_url(
    category: DongguCategory | str, identity: str
) -> str:
    item = _CATEGORY_BY_CODE[category] if isinstance(category, str) else category
    sid = _clean(identity)
    if not _IDENTITY_RE.fullmatch(sid):
        return ""
    return f"{BUSAN_DONGGU_URL}?{urlencode((('menuCd', item.application_menu), ('data_Sid', sid)))}"


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
            "Referer": BUSAN_DONGGU_URL,
        }
    )
    return current


def _default_fetcher(current_session: Any, url: str, timeout: int) -> Any:
    response = current_session.get(url, timeout=timeout, allow_redirects=False)
    if int(getattr(response, "status_code", 0)) != 200:
        raise ValueError(f"unexpected HTTP status {getattr(response, 'status_code', None)}")
    if getattr(response, "headers", {}).get("Location"):
        raise ValueError("redirect response is not accepted")
    if not getattr(response, "content", b""):
        raise ValueError("empty HTTP response")
    return response


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, bytes):
        return BeautifulSoup(value, "lxml")
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if content is None:
        raise TypeError("fetcher did not return HTML or an HTTP response")
    return BeautifulSoup(content, "lxml")


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _title_owned(soup: BeautifulSoup, category: Optional[DongguCategory] = None) -> bool:
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    compact = title.replace(" ", "")
    if "부산광역시동구통합예약" not in compact:
        return False
    return category is None or category.label.replace(" ", "") in compact


def _root_owned(soup: BeautifulSoup) -> bool:
    if not _title_owned(soup):
        return False
    owned: set[str] = set()
    for anchor in soup.select("a[href*='menuCd=']"):
        parsed = urlparse(urljoin(BUSAN_DONGGU_URL, _clean(anchor.get("href"))))
        query = parse_qs(parsed.query, keep_blank_values=True)
        menus = query.get("menuCd") or []
        if (
            parsed.scheme == "https"
            and parsed.hostname == BUSAN_DONGGU_HOST
            and parsed.path == BUSAN_DONGGU_PATH
            and len(menus) == 1
        ):
            owned.add(_clean(menus[0]))
    return all(item.root_menu in owned for item in BUSAN_DONGGU_CATEGORIES)


def _query_one(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _clean(values[0]) if len(values) == 1 else ""


def _paging_number(href: Any, category: DongguCategory) -> Optional[int]:
    parsed = urlparse(urljoin(BUSAN_DONGGU_URL, _clean(href)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    page = _query_one(query, "page_no")
    if (
        parsed.scheme != "https"
        or parsed.hostname != BUSAN_DONGGU_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != BUSAN_DONGGU_PATH
        or parsed.params
        or parsed.fragment
        or set(query) - _PAGING_ALLOWED_QUERY
        or _query_one(query, "menuCd") != category.list_menu
        or _query_one(query, "search_Status") != "T"
        or not page.isdigit()
        or int(page) < 1
    ):
        return None
    return int(page)


def _page_contract(
    soup: BeautifulSoup, category: DongguCategory
) -> Optional[tuple[int, int]]:
    if not _title_owned(soup, category):
        return None
    active = soup.select(".paging li.on a[href]")
    if len(active) != 1:
        return None
    current = _paging_number(active[0].get("href"), category)
    if current is None:
        return None
    numbers: list[int] = []
    for anchor in soup.select(".paging a[href]"):
        number = _paging_number(anchor.get("href"), category)
        if number is None:
            return None
        numbers.append(number)
    last_images = soup.select(".paging img[alt='마지막 페이지']")
    if len(last_images) > 1:
        return None
    if last_images:
        parent = last_images[0].find_parent("a", href=True)
        total = _paging_number(parent.get("href"), category) if parent else None
    else:
        total = max(numbers, default=current)
    if total is None or total < current:
        return None
    return current, total


def _detail_identity(value: Any, category: DongguCategory) -> str:
    parsed = urlparse(urljoin(BUSAN_DONGGU_URL, _clean(value)))
    query = parse_qs(parsed.query, keep_blank_values=True)
    sid = _query_one(query, "data_Sid")
    if (
        parsed.scheme != "https"
        or parsed.hostname != BUSAN_DONGGU_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != BUSAN_DONGGU_PATH
        or parsed.params
        or parsed.fragment
        or set(query) - _DETAIL_ALLOWED_QUERY
        or _query_one(query, "menuCd") != category.detail_menu
        or not _IDENTITY_RE.fullmatch(sid)
    ):
        return ""
    return sid


def _li_pairs(card: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in card.select("li"):
        label_node = item.select_one(".name")
        if label_node is None:
            continue
        label = _clean(label_node.get_text(" ", strip=True))
        clone = BeautifulSoup(str(item), "lxml")
        clone_label = clone.select_one(".name")
        if clone_label is not None:
            clone_label.extract()
        if label:
            pairs[label] = _clean(clone.get_text(" ", strip=True))
    return pairs


def _compact_range(value: Any) -> tuple[str, str, str]:
    match = _COMPACT_DATE_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        return "", "", ""
    try:
        start = date(*(int(part) for part in match.groups()[:3]))
        end = date(*(int(part) for part in match.groups()[3:]))
    except ValueError:
        return "", "", ""
    if end < start:
        return "", "", ""
    return start.isoformat(), end.isoformat(), f"{start.isoformat()} ~ {end.isoformat()}"


def _date_from_text(value: Any) -> str:
    match = _DATE_RE.search(_clean(value))
    if match is None:
        return ""
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return ""


def _fractions(value: Any) -> list[tuple[int, int]]:
    return [(int(left), int(right)) for left, right in _FRACTION_RE.findall(_clean(value))]


def _parse_list_page(
    target: Any,
    soup: BeautifulSoup,
    category: DongguCategory,
    page_no: int,
    total_pages: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, card in enumerate(soup.select(".bbs_ltype2 > dl"), start=1):
        prefix = f"card {index}"
        status_nodes = card.select("dt .mark a.lectureBtn[onclick*='fnRegProc']")
        detail_nodes = [
            node
            for node in card.select("dt > a[href]")
            if "data_Sid=" in _clean(node.get("href"))
        ]
        if len(status_nodes) != 1 or len(detail_nodes) != 1:
            errors.append(f"{prefix}: missing unique status/detail control")
            continue
        source_status = _clean(status_nodes[0].get_text(" ", strip=True))
        normalized_status = _STATUS_MAP.get(source_status, "")
        control = _REG_PROC_RE.search(_clean(status_nodes[0].get("onclick")))
        identity = _detail_identity(detail_nodes[0].get("href"), category)
        if not normalized_status:
            errors.append(f"{prefix}: unknown source status {source_status!r}")
        if control is None or not identity or (control and control.group(1) != identity):
            errors.append(f"{prefix}: malformed or mismatched stable identity")
        if not identity:
            continue

        source_available = bool(control and control.group(2).lower() == "true")
        expected_available = normalized_status == "OPEN"
        if normalized_status and source_available != expected_available:
            errors.append(f"{prefix}: status/application control disagreement")

        title = _clean(detail_nodes[0].get_text(" ", strip=True))
        pairs = _li_pairs(card)
        start_date, end_date, period = _compact_range(pairs.get("교육기간"))
        apply_start, apply_end, apply_period = _compact_range(pairs.get("접수기간"))
        historical_invalid = bool(
            page_no == total_pages
            and normalized_status == "CLOSED"
            and (not title or not start_date or not end_date)
        )
        if (not title or not start_date or not end_date) and not historical_invalid:
            errors.append(f"{prefix}: missing title or valid education period")
        if not historical_invalid and (not apply_start or not apply_end):
            errors.append(f"{prefix}: missing valid application period")

        capacity_values = _fractions(pairs.get("신청/모집"))
        if not historical_invalid and len(capacity_values) != 2:
            errors.append(f"{prefix}: malformed capacity/waitlist values")
        capacity_current, capacity_total = capacity_values[0] if capacity_values else (0, 0)
        wait_current, wait_total = capacity_values[1] if len(capacity_values) > 1 else (0, 0)
        venue = _clean(pairs.get("교육장소"))
        provisional_branch = _branch_for(category, venue, title)
        message = _clean(control.group(0) if control else "")
        row: dict[str, Any] = {
            "provider": BUSAN_DONGGU_PROVIDER,
            "provider_course_id": f"{BUSAN_DONGGU_PROVIDER}:course:{identity}",
            "prefer_incoming_provider_course_id": True,
            "title": title,
            "branch": provisional_branch,
            "branch_code": _branch_code(provisional_branch),
            "preserve_branch": True,
            "category": category.label,
            "raw_url": busan_donggu_detail_url(category, identity),
            "status": normalized_status,
            "start_date": start_date,
            "end_date": end_date,
            "period": period,
            "apply_start_date": apply_start,
            "apply_end_date": apply_end,
            "apply_period": apply_period,
            "target": _clean(pairs.get("교육대상")),
            "fee": _clean(pairs.get("기타경비")),
            "capacity": _clean(pairs.get("신청/모집")),
            "capacity_current": capacity_current,
            "capacity_total": capacity_total,
            "waitlist_current": wait_current,
            "waitlist_total": wait_total,
            "room": venue,
            "venue_name": venue,
            "collection_category": "공공예약",
            "domain_category": "교육/강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "public_reservation",
            "collection_type": "static_html+detail_html",
            "program_type": "교육",
            "reservation_available": source_available,
            "raw_fields": {
                "municipality_code": BUSAN_DONGGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_DONGGU_MUNICIPALITY_NAME,
                "source_catalog": "donggu_five_category_catalogue",
                "category_code": category.code,
                "category_name": category.label,
                "data_sid": identity,
                "source_status": source_status,
                "source_available": source_available,
                "application_control": message,
                "list_pairs": pairs,
                "list_page": page_no,
                "historical_invalid": historical_invalid,
                "parser": BUSAN_DONGGU_PARSER,
            },
        }
        if source_available:
            row["application_url"] = busan_donggu_application_url(category, identity)
            row["application_type"] = "ONLINE_RESERVATION"
        else:
            row["raw_fields"]["clear_application_url"] = True
        rows.append(row)
    return rows, errors


def _detail_values(
    soup: BeautifulSoup,
) -> tuple[list[str], dict[str, str], set[str], list[str]]:
    """Read only allowlisted district detail values.

    Labels are contract data and may be inspected.  Values beside contact,
    instructor, attachment, free-form description, or location-widget labels
    are deliberately never materialized.
    """

    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    errors: list[str] = []
    for heading in soup.select("table th"):
        value = heading.find_next_sibling("td")
        if value is None:
            errors.append("detail label lacks an adjacent value cell")
            continue
        label = _clean(heading.get_text(" ", strip=True)).rstrip(":")
        if not label:
            errors.append("detail contains an empty label")
        elif label in labels:
            errors.append(f"duplicate detail label {label!r}")
        elif label in _DISTRICT_DETAIL_SAFE_LABELS:
            labels.append(label)
            safe[label] = _clean(value.get_text(" ", strip=True))
        elif label in _DISTRICT_DETAIL_SKIPPED_LABELS or label.startswith("첨부파일"):
            labels.append(label)
            skipped.add(label)
        else:
            labels.append(label)
            errors.append(f"unknown district detail label {label!r}")
    return labels, safe, skipped, errors


def _first_pair(pairs: Mapping[str, str], *labels: str) -> str:
    for label in labels:
        value = _clean(pairs.get(label))
        if value:
            return value
    return ""


def _branch_for(category: DongguCategory, venue: str, title: str) -> str:
    if category.code == "706":
        return "동구어린이영어도서관"
    combined = _clean(f"{title} {venue}")
    institutions = (
        ("수정5동 실버특화작은도서관", "수정5동 실버특화작은도서관"),
        ("시민마당 들락날락 어린이", "동구 시민마당 들락날락 어린이작은도서관"),
        ("시민마당 들락날락", "동구 시민마당 들락날락 어린이작은도서관"),
        ("수남어린이작은도서관", "수남어린이작은도서관"),
        ("더나눔어린이작은도서관", "더나눔어린이작은도서관"),
        ("호랭이마을꿈터작은도서관", "호랭이마을꿈터작은도서관"),
        ("초량이바구작은도서관", "초량이바구작은도서관"),
        ("동구어린이영어도서관", "동구어린이영어도서관"),
        ("동구도서관", "동구도서관"),
        ("동구여성인력개발센터", "동구여성인력개발센터"),
        ("이바구생활문화센터", "이바구생활문화센터"),
        ("이바구 생활문화센터", "이바구생활문화센터"),
        ("동구종합사회복지관", "동구종합사회복지관"),
        ("이바구복합문화센터", "이바구복합문화센터"),
    )
    for needle, branch in institutions:
        if needle in combined:
            return branch
    if category.code == "701":
        return "온라인" if "온라인" in combined else category.default_branch
    if category.code in {"702", "703"}:
        return category.default_branch
    if venue:
        concise = _clean(re.split(r"[,，]", venue, maxsplit=1)[0])
        return concise or category.default_branch
    return category.default_branch


def _branch_code(branch: str) -> str:
    digest = hashlib.sha1(_clean(branch).encode("utf-8")).hexdigest()[:12].upper()
    return f"BSDG_{digest}"


def _enrich_detail(
    row: dict[str, Any], soup: BeautifulSoup, cutoff: date
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    raw_fields = row.get("raw_fields") or {}
    category = _CATEGORY_BY_CODE.get(_clean(raw_fields.get("category_code")))
    identity = _clean(raw_fields.get("data_sid"))
    if category is None or not _title_owned(soup, category):
        return False, [f"course {identity}: detail ownership mismatch"]
    labels, pairs, skipped, field_errors = _detail_values(soup)
    errors.extend(f"course {identity}: {message}" for message in field_errors)
    detail_title = _first_pair(pairs, "강좌명", "접수명")
    detail_start = _date_from_text(_first_pair(pairs, "교육시작일", "운영시작일"))
    detail_end = _date_from_text(_first_pair(pairs, "교육종료일", "운영종료일"))
    apply_start = _date_from_text(_first_pair(pairs, "접수시작일"))
    apply_end = _date_from_text(_first_pair(pairs, "접수종료일"))
    if detail_title != _clean(row.get("title")):
        errors.append(f"course {identity}: detail title mismatch")
    if (detail_start, detail_end) != (
        _clean(row.get("start_date")),
        _clean(row.get("end_date")),
    ):
        errors.append(f"course {identity}: detail education period mismatch")
    if (apply_start, apply_end) != (
        _clean(row.get("apply_start_date")),
        _clean(row.get("apply_end_date")),
    ):
        errors.append(f"course {identity}: detail application period mismatch")

    # The official detail template removed the historical ``lectureBtn``
    # class in July 2026 while retaining the same server-owned fnRegProc
    # contract.  Bind to that exact function and validate its identity below.
    controls = soup.select("a[onclick*='fnRegProc']")
    parsed_controls = [
        _REG_PROC_RE.search(_clean(node.get("onclick"))) for node in controls
    ]
    parsed_controls = [item for item in parsed_controls if item is not None]
    if len(parsed_controls) != 1 or parsed_controls[0].group(1) != identity:
        errors.append(f"course {identity}: detail application identity mismatch")
        detail_available = False
        application_message = ""
    else:
        detail_available = parsed_controls[0].group(2).lower() == "true"
        application_message = _clean(controls[0].get("onclick"))
    expected_available = _clean(row.get("status")) == "OPEN"
    if detail_available != expected_available:
        errors.append(f"course {identity}: detail application state mismatch")

    capacity = _fractions(_first_pair(pairs, "신청가능인원"))
    waitlist = _fractions(_first_pair(pairs, "대기가능인원"))
    if len(capacity) != 1 or capacity[0] != (
        int(row.get("capacity_current") or 0),
        int(row.get("capacity_total") or 0),
    ):
        errors.append(f"course {identity}: detail capacity mismatch")
    if len(waitlist) != 1 or waitlist[0] != (
        int(row.get("waitlist_current") or 0),
        int(row.get("waitlist_total") or 0),
    ):
        errors.append(f"course {identity}: detail waitlist mismatch")

    venue = _first_pair(pairs, "교육장소", "장소") or _clean(row.get("room"))
    branch = _branch_for(category, venue, detail_title or _clean(row.get("title")))
    schedule = _first_pair(pairs, "교육시간", "운영시간")
    row.update(
        {
            "branch": branch,
            "branch_code": _branch_code(branch),
            "room": venue,
            "venue_name": venue,
            "venue_address": _first_pair(pairs, "교육장소주소"),
            "address": _first_pair(pairs, "교육장소주소"),
            "target": _first_pair(pairs, "교육대상", "운영대상")
            or row.get("target"),
            "schedule_raw": schedule,
            "description": _clean(row.get("title")),
            "fee": _first_pair(pairs, "기타경비") or row.get("fee"),
            "reservation_available": detail_available,
        }
    )
    if detail_available:
        row["application_url"] = busan_donggu_application_url(category, identity)
        row["application_type"] = "ONLINE_RESERVATION"
    else:
        row.pop("application_url", None)
        row.pop("application_type", None)
        raw_fields["clear_application_url"] = True
    raw_fields.update(
        {
            "detail_labels": labels,
            "detail_identity_verified": not errors,
            "detail_application_control": application_message,
            "contact_value_never_read": bool(
                {"교육문의전화", "문의전화"} & skipped
            ),
            "instructor_value_never_read": "강사명" in skipped,
            "free_form_detail_never_read": bool(
                {"강좌내용", "접수내용"} & skipped
            ),
            "attachments_never_read": any(
                label.startswith("첨부파일") for label in skipped
            ),
            "application_form_fetched": False,
            "applicant_list_fetched": False,
        }
    )
    row["raw_fields"] = raw_fields
    try:
        is_current = date.fromisoformat(detail_end) >= cutoff
    except ValueError:
        is_current = False
        errors.append(f"course {identity}: invalid detail end date")
    return is_current, errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _failure_meta(message: str, *, source_cap_reached: bool = False) -> dict[str, Any]:
    return {
        "pages": 0,
        "list_pages": 0,
        "list_requests": 0,
        "sentinel_requests": 0,
        "detail_pages": 0,
        "detail_required_count": 0,
        "total_count": 0,
        "current_count": 0,
        "returned_count": 0,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": source_cap_reached,
        "no_current_data": False,
        "configured_collection_error": message,
    }


def _collect_busan_donggu_district_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 300,
    detail_limit: int = 100,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BUSAN_DONGGU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect the district five-menu ledger for the atomic wrapper."""

    if not is_busan_donggu_target(target):
        return [], BUSAN_DONGGU_PARSER, _failure_meta(
            "target does not match the exact Busan Dong-gu integrated reservation route"
        )

    current_fetcher = fetcher or _default_fetcher
    current_session_factory = session_factory or _default_session_factory
    current_dedupe = dedupe_rows or _dedupe_default
    cutoff = _today(today)
    errors: list[str] = []
    detail_errors: list[str] = []
    source_cap_reached = False
    sessions: list[Any] = []
    sessions_lock = threading.Lock()
    local = threading.local()

    def thread_session() -> Any:
        value = getattr(local, "session", None)
        count = int(getattr(local, "request_count", 0))
        if value is None or count >= BUSAN_DONGGU_SESSION_REQUEST_LIMIT:
            if value is not None:
                _close_quietly(value)
            value = current_session_factory()
            local.session = value
            local.request_count = 0
            with sessions_lock:
                sessions.append(value)
        local.request_count = int(getattr(local, "request_count", 0)) + 1
        return value

    def fetch_url(url: str) -> BeautifulSoup:
        return _coerce_soup(current_fetcher(thread_session(), url, timeout))

    page_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
    totals: dict[str, int] = {}
    list_requests = 0
    sentinel_requests = 0
    page_one_rechecks = 0

    try:
        try:
            root = fetch_url(BUSAN_DONGGU_URL)
        except Exception as exc:
            return [], BUSAN_DONGGU_PARSER, _failure_meta(
                f"official root fetch failed: {type(exc).__name__}"
            )
        if not _root_owned(root):
            return [], BUSAN_DONGGU_PARSER, _failure_meta(
                "official root ownership/navigation contract failed"
            )

        for category in BUSAN_DONGGU_CATEGORIES:
            try:
                soup = fetch_url(busan_donggu_list_url(category, 1))
                list_requests += 1
            except Exception as exc:
                errors.append(f"{category.label} page 1: fetch {type(exc).__name__}")
                continue
            contract = _page_contract(soup, category)
            if contract is None or contract[0] != 1:
                errors.append(f"{category.label} page 1: malformed pagination/ownership")
                continue
            total = contract[1]
            totals[category.code] = total
            if int(max_pages) < total:
                source_cap_reached = True
                errors.append(
                    f"{category.label}: max_pages cap {int(max_pages)} is below "
                    f"declared {total} pages"
                )
                continue
            parsed_rows, page_errors = _parse_list_page(
                target, soup, category, 1, total
            )
            errors.extend(f"{category.label} page 1: {item}" for item in page_errors)
            page_rows[(category.code, 1)] = parsed_rows

        if errors:
            meta = _failure_meta(
                "; ".join(dict.fromkeys(errors)),
                source_cap_reached=source_cap_reached,
            )
            meta.update(
                {
                    "pages": 1 + list_requests,
                    "list_requests": list_requests,
                    "category_page_counts": totals,
                }
            )
            return [], BUSAN_DONGGU_PARSER, meta

        tasks = [
            (category, page_no, page_no == totals[category.code] + 1)
            for category in BUSAN_DONGGU_CATEGORIES
            for page_no in range(2, totals[category.code] + 2)
        ]

        def fetch_page(
            task: tuple[DongguCategory, int, bool]
        ) -> tuple[DongguCategory, int, bool, Optional[BeautifulSoup], str]:
            category, page_no, sentinel = task
            try:
                return category, page_no, sentinel, fetch_url(
                    busan_donggu_list_url(category, page_no)
                ), ""
            except Exception as exc:
                return (
                    category,
                    page_no,
                    sentinel,
                    None,
                    f"fetch {type(exc).__name__}",
                )

        if tasks:
            workers = min(max(1, int(max_workers)), BUSAN_DONGGU_MAX_WORKERS, len(tasks))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="busan-donggu-list"
            ) as pool:
                results = list(pool.map(fetch_page, tasks))
            for category, page_no, sentinel, soup, fetch_error in results:
                if sentinel:
                    sentinel_requests += 1
                else:
                    list_requests += 1
                if fetch_error or soup is None:
                    errors.append(
                        f"{category.label} page {page_no}: {fetch_error or 'empty response'}"
                    )
                    continue
                if sentinel:
                    if not _title_owned(soup, category):
                        errors.append(
                            f"{category.label} sentinel {page_no}: ownership mismatch"
                        )
                    if soup.select(".bbs_ltype2 > dl"):
                        errors.append(
                            f"{category.label} sentinel {page_no}: unexpected rows"
                        )
                    continue
                contract = _page_contract(soup, category)
                expected = (page_no, totals[category.code])
                if contract != expected:
                    errors.append(
                        f"{category.label} page {page_no}: pagination {contract!r} != {expected!r}"
                    )
                    continue
                parsed_rows, page_errors = _parse_list_page(
                    target, soup, category, page_no, totals[category.code]
                )
                errors.extend(
                    f"{category.label} page {page_no}: {item}" for item in page_errors
                )
                page_rows[(category.code, page_no)] = parsed_rows

        for category in BUSAN_DONGGU_CATEGORIES:
            total = totals[category.code]
            for page_no in range(1, total + 1):
                count = len(page_rows.get((category.code, page_no), []))
                if page_no < total and count != category.page_size:
                    errors.append(
                        f"{category.label} page {page_no}: exposed {count} rows, "
                        f"expected {category.page_size}"
                    )
                elif page_no == total and not (1 <= count <= category.page_size):
                    errors.append(
                        f"{category.label} last page {page_no}: invalid row count {count}"
                    )

        def recheck_page_one(
            category: DongguCategory,
        ) -> tuple[DongguCategory, Optional[BeautifulSoup], str]:
            try:
                return category, fetch_url(busan_donggu_list_url(category, 1)), ""
            except Exception as exc:
                return category, None, f"fetch {type(exc).__name__}"

        workers = min(max(1, int(max_workers)), len(BUSAN_DONGGU_CATEGORIES))
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="busan-donggu-recheck"
        ) as pool:
            rechecks = list(pool.map(recheck_page_one, BUSAN_DONGGU_CATEGORIES))
        page_one_rechecks = len(rechecks)
        for category, soup, fetch_error in rechecks:
            if fetch_error or soup is None:
                errors.append(f"{category.label} page 1 recheck: {fetch_error}")
                continue
            contract = _page_contract(soup, category)
            checked, checked_errors = _parse_list_page(
                target, soup, category, 1, totals[category.code]
            )
            errors.extend(
                f"{category.label} page 1 recheck: {item}" for item in checked_errors
            )
            first_ids = [
                _clean(row.get("raw_fields", {}).get("data_sid"))
                for row in page_rows.get((category.code, 1), [])
            ]
            checked_ids = [
                _clean(row.get("raw_fields", {}).get("data_sid")) for row in checked
            ]
            if contract != (1, totals[category.code]) or checked_ids != first_ids:
                errors.append(
                    f"{category.label}: page 1 changed during complete traversal"
                )

        all_rows = [
            row
            for category in BUSAN_DONGGU_CATEGORIES
            for page_no in range(1, totals[category.code] + 1)
            for row in page_rows.get((category.code, page_no), [])
        ]
        identities = [
            _clean(row.get("raw_fields", {}).get("data_sid")) for row in all_rows
        ]
        duplicate_count = len(identities) - len(set(identities))
        if not identities or any(not identity for identity in identities):
            errors.append("one or more source rows lacks a stable data_Sid")
        if duplicate_count:
            errors.append(f"duplicate stable identities across five categories: {duplicate_count}")

        historical_invalid = [
            row
            for row in all_rows
            if bool(row.get("raw_fields", {}).get("historical_invalid"))
        ]
        valid_rows = [row for row in all_rows if row not in historical_invalid]
        current_rows_by_list: list[dict[str, Any]] = []
        expired_count = 0
        for row in valid_rows:
            try:
                end = date.fromisoformat(_clean(row.get("end_date")))
            except ValueError:
                errors.append(
                    f"course {row.get('raw_fields', {}).get('data_sid')}: invalid list end date"
                )
                continue
            if end >= cutoff:
                current_rows_by_list.append(row)
            else:
                expired_count += 1

        detail_required_count = len(current_rows_by_list)
        if int(detail_limit) < detail_required_count:
            source_cap_reached = True
            errors.append(
                f"detail_limit cap {int(detail_limit)} is below required "
                f"{detail_required_count} details"
            )

        detail_pages = 0
        current_rows: list[dict[str, Any]] = []
        if not errors and current_rows_by_list:

            def fetch_detail(
                row: dict[str, Any],
            ) -> tuple[dict[str, Any], bool, bool, list[str]]:
                identity = _clean(row.get("raw_fields", {}).get("data_sid"))
                try:
                    soup = fetch_url(_clean(row.get("raw_url")))
                    is_current, item_errors = _enrich_detail(row, soup, cutoff)
                    return row, True, is_current, item_errors
                except Exception as exc:
                    return row, False, False, [
                        f"course {identity}: detail fetch {type(exc).__name__}"
                    ]

            workers = min(
                max(1, int(max_workers)),
                BUSAN_DONGGU_MAX_WORKERS,
                detail_required_count,
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="busan-donggu-detail"
            ) as pool:
                detail_results = list(pool.map(fetch_detail, current_rows_by_list))
            for row, fetched, is_current, item_errors in detail_results:
                detail_pages += int(fetched)
                detail_errors.extend(item_errors)
                if is_current:
                    current_rows.append(row)

        errors.extend(detail_errors)
        if len(current_rows) != detail_required_count:
            errors.append(
                f"detail current count {len(current_rows)} != required {detail_required_count}"
            )

        semantic_keys: dict[tuple[str, ...], list[str]] = {}
        for row in current_rows:
            key = (
                re.sub(r"[^0-9a-z가-힣]+", "", _clean(row.get("title")).lower()),
                _clean(row.get("start_date")),
                _clean(row.get("end_date")),
                _clean(row.get("branch")),
                _clean(row.get("schedule_raw")),
            )
            semantic_keys.setdefault(key, []).append(
                _clean(row.get("raw_fields", {}).get("data_sid"))
            )
        semantic_duplicates = {
            key: values for key, values in semantic_keys.items() if len(values) > 1
        }
        if semantic_duplicates:
            errors.append(
                "semantic duplicate current programs: "
                f"{sum(len(v) - 1 for v in semantic_duplicates.values())}"
            )

        cleaned = current_rows
        if not errors:
            try:
                deduped = list(current_dedupe(cleaned))
            except Exception as exc:
                errors.append(f"dedupe failed {type(exc).__name__}")
                deduped = []
            if len(deduped) != len(cleaned):
                errors.append(
                    f"dedupe changed complete row count {len(cleaned)} to {len(deduped)}"
                )
            cleaned = deduped

        pagination_complete = (
            not errors
            and len(page_rows) == sum(totals.values())
            and sentinel_requests == len(BUSAN_DONGGU_CATEGORIES)
            and page_one_rechecks == len(BUSAN_DONGGU_CATEGORIES)
        )
        details_complete = (
            not detail_errors
            and not source_cap_reached
            and detail_pages == detail_required_count
            and len(current_rows) == detail_required_count
        )
        snapshot_complete = pagination_complete and details_complete and not errors
        if not snapshot_complete:
            cleaned = []

        category_source_counts = Counter(
            _clean(row.get("raw_fields", {}).get("category_name")) for row in all_rows
        )
        category_current_counts = Counter(
            _clean(row.get("raw_fields", {}).get("category_name"))
            for row in current_rows
        )
        status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status")) for row in all_rows
        )
        current_status_counts = Counter(
            _clean(row.get("raw_fields", {}).get("source_status"))
            for row in current_rows
        )
        branch_counts = Counter(_clean(row.get("branch")) for row in current_rows)
        list_pages = sum(totals.values())
        meta: dict[str, Any] = {
            "pages": 1
            + list_requests
            + sentinel_requests
            + page_one_rechecks
            + detail_pages,
            "request_count": 1
            + list_requests
            + sentinel_requests
            + page_one_rechecks
            + detail_pages,
            "root_requests": 1,
            "list_pages": list_pages,
            "list_requests": list_requests,
            "sentinel_requests": sentinel_requests,
            "page_one_rechecks": page_one_rechecks,
            "detail_pages": detail_pages,
            "detail_required_count": detail_required_count,
            "detail_attempts": detail_required_count if not source_cap_reached else 0,
            "total_count": len(all_rows),
            "source_exposed_count": len(all_rows),
            "unique_id_count": len(set(identities)),
            # Private hand-off to the atomic wrapper.  It is removed before
            # public metadata is returned and proves platform external rows
            # are exact republications, never fuzzy title matches.
            "_district_source_identities": tuple(identities),
            "duplicate_count": duplicate_count,
            "semantic_duplicate_count": sum(
                len(values) - 1 for values in semantic_duplicates.values()
            ),
            "historical_invalid_count": len(historical_invalid),
            "valid_history_count": len(valid_rows),
            "expired_count": expired_count,
            "current_count": len(current_rows),
            "returned_count": len(cleaned),
            "category_page_counts": {
                item.label: totals.get(item.code, 0)
                for item in BUSAN_DONGGU_CATEGORIES
            },
            "category_source_counts": dict(category_source_counts),
            "category_current_counts": dict(category_current_counts),
            "page_row_counts": {
                f"{item.label}:{page_no}": len(
                    page_rows.get((item.code, page_no), [])
                )
                for item in BUSAN_DONGGU_CATEGORIES
                for page_no in range(1, totals.get(item.code, 0) + 1)
            },
            "status_counts": dict(status_counts),
            "current_status_counts": dict(current_status_counts),
            "branch_counts": dict(branch_counts),
            "pagination_detected": list_pages > len(BUSAN_DONGGU_CATEGORIES),
            "pagination_complete": pagination_complete,
            "pagination_exhausted": pagination_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "full_snapshot_validated": snapshot_complete,
            "source_cap_reached": source_cap_reached,
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in current_rows
            ),
            "recursion_depth": 0,
            "no_current_data": snapshot_complete and not current_rows,
            "no_current_reason": (
                "all five complete Busan Dong-gu education categories have no current/future rows"
                if snapshot_complete and not current_rows
                else ""
            ),
        }
        if errors:
            meta["configured_collection_error"] = "; ".join(
                dict.fromkeys(errors)
            )
        return cleaned, BUSAN_DONGGU_PARSER, meta
    finally:
        for value in sessions:
            _close_quietly(value)


class BusanDongguContractError(ValueError):
    """Raised when an audited owner or source contract changes."""


def _text(node: Any) -> str:
    return _clean(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""


def _one(values: Iterable[Any], label: str) -> Any:
    found = list(values)
    if len(found) != 1:
        raise BusanDongguContractError(
            f"expected one {label}, found {len(found)}"
        )
    return found[0]


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise BusanDongguContractError(f"{label} cannot be boolean")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BusanDongguContractError(f"invalid {label}") from exc
    if parsed < 1:
        raise BusanDongguContractError(f"{label} must be positive")
    return parsed


def busan_donggu_lifelong_list_url(page: int = 1) -> str:
    value = _positive_int(page, "lifelong page")
    payload = _lifelong._list_payload(BUSAN_LIFELONG_DONGGU_OFFICE, value)
    payload["pageUnit"] = str(BUSAN_LIFELONG_PAGE_SIZE)
    return BUSAN_LIFELONG_LIST_URL + "?" + urlencode(payload)


def busan_donggu_lifelong_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _LEARNING_ID_RE.fullmatch(value):
        raise BusanDongguContractError("invalid lifelong identity")
    return _lifelong.busan_lifelong_detail_url(value)


def busan_donggu_city_list_url(page: int = 1) -> str:
    value = _positive_int(page, "city page")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_LIST_PATH}?" + urlencode(
        (
            ("curPage", value),
            ("srchGugun", BUSAN_CITY_DONGGU_GUGUN),
            ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
        )
    )


def busan_donggu_city_detail_url(group_id: Any, program_id: Any) -> str:
    group = _clean(group_id)
    program = _clean(program_id)
    if not group.isdigit() or not program.isdigit():
        raise BusanDongguContractError("invalid city identity")
    return f"https://{BUSAN_CITY_HOST}{BUSAN_CITY_DETAIL_PATH}?" + urlencode(
        (("resveGroupSn", group), ("progrmSn", program))
    )


def _exact_response_scope(
    final_url: str, host: str, path: str
) -> Mapping[str, list[str]]:
    parsed = urlparse(_clean(final_url))
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != host
        or parsed.port is not None
        or parsed.path != path
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise BusanDongguContractError("response escaped the audited URL scope")
    return parse_qs(parsed.query, keep_blank_values=True)


def canonical_busan_donggu_course_identity(value: Any) -> str:
    parsed = urlparse(_clean(value))
    query = parse_qs(parsed.query, keep_blank_values=True)
    menu = _query_one(query, "menuCd")
    identity = _query_one(query, "data_Sid")
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").rstrip(".").lower() != BUSAN_DONGGU_HOST
        or parsed.port is not None
        or parsed.path != BUSAN_DONGGU_PATH
        or parsed.params
        or parsed.fragment
        or parsed.username
        or parsed.password
        or set(query) - _DETAIL_ALLOWED_QUERY
        or menu not in {item.detail_menu for item in BUSAN_DONGGU_CATEGORIES}
        or not _IDENTITY_RE.fullmatch(identity)
    ):
        return ""
    return f"data_sid:{identity}"


def _platform_office() -> _lifelong.BusanOffice:
    office = _lifelong.BUSAN_LIFELONG_OFFICE_BY_CODE.get(
        BUSAN_LIFELONG_DONGGU_OFFICE
    )
    if office is None or office.name != BUSAN_LIFELONG_DONGGU_OFFICE_NAME:
        raise BusanDongguContractError("lifelong Dong-gu office changed")
    if (
        office.ownership != BUSAN_LIFELONG_DONGGU_EXPECTED_OWNERSHIP
        or office.municipality_code
        or office.municipality_name
    ):
        raise BusanDongguContractError("lifelong Dong-gu ownership changed")
    return office


def _parse_platform_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    expected_query = {
        key: [value]
        for key, value in _lifelong._list_payload(
            BUSAN_LIFELONG_DONGGU_OFFICE, page
        ).items()
    }
    expected_query["pageUnit"] = [str(BUSAN_LIFELONG_PAGE_SIZE)]
    if _exact_response_scope(
        final_url, _lifelong.BUSAN_LIFELONG_HOST, BUSAN_LIFELONG_LIST_PATH
    ) != expected_query:
        raise BusanDongguContractError("lifelong list response query changed")
    office = _platform_office()
    form_errors = _lifelong._form_errors(soup, office, page)
    if form_errors:
        raise BusanDongguContractError("; ".join(form_errors))
    last, last_errors = _lifelong._advertised_last(soup)
    if last_errors:
        raise BusanDongguContractError("; ".join(last_errors))
    if expected_last is not None and last != expected_last:
        raise BusanDongguContractError("lifelong final page changed")
    rows, errors = _lifelong._parse_list_page(soup, office=office, page=page)
    if errors:
        raise BusanDongguContractError("; ".join(errors))
    if page <= last:
        if not rows:
            raise BusanDongguContractError("lifelong data page became empty")
        if page < last and len(rows) != BUSAN_LIFELONG_PAGE_SIZE:
            raise BusanDongguContractError("lifelong intermediate page is short")
        if len(rows) > BUSAN_LIFELONG_PAGE_SIZE:
            raise BusanDongguContractError("lifelong page exceeds requested pageUnit")
    elif page == last + 1:
        if rows:
            raise BusanDongguContractError("lifelong sentinel is not empty")
    else:
        raise BusanDongguContractError("lifelong request passed sentinel")
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
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    )


def _platform_external_datasid(row: Mapping[str, Any]) -> str:
    if _clean(row.get("raw_fields", {}).get("identity_kind")) != "external":
        raise BusanDongguContractError("lifelong row is not external")
    identity = canonical_busan_donggu_course_identity(row.get("raw_url"))
    if not identity.startswith("data_sid:"):
        raise BusanDongguContractError(
            "lifelong external row left the district detail scope"
        )
    return identity.removeprefix("data_sid:")


def _platform_native_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(row.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if raw.get("identity_kind") != "internal" or not _LEARNING_ID_RE.fullmatch(
        identity
    ):
        raise BusanDongguContractError("invalid native lifelong identity")
    result = dict(row)
    result.update(
        {
            "provider": BUSAN_DONGGU_PROVIDER,
            "provider_course_id": (
                f"{BUSAN_DONGGU_PROVIDER}:lifelong:{identity}"
            ),
            "prefer_incoming_provider_course_id": True,
            "branch": BUSAN_LIFELONG_DONGGU_OFFICE_NAME,
            "branch_code": "donggu-lifelong-office00002642",
            "preserve_branch": True,
            "provider_organizer": BUSAN_LIFELONG_DONGGU_OFFICE_NAME,
            "municipality_code": BUSAN_DONGGU_MUNICIPALITY_CODE,
            "municipality_name": BUSAN_DONGGU_MUNICIPALITY_NAME,
            "sido": "부산광역시",
            "sigungu": "동구",
            "collection_category": "공공예약",
            "domain_category": "교육·강좌",
            "operator_type": "지자체/공공기관",
            "source_group": "municipal_reservation",
            "service_group": "공공강좌",
            "service_group_policy": "locked",
            "collection_type": (
                "complete_shared_office_census+native_current_safe_detail"
            ),
        }
    )
    result["raw_fields"] = {
        **raw,
        "parser": BUSAN_DONGGU_PARSER,
        "source_catalog": "busan_lifelong_donggu_native",
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
        heading = _one(
            definition.find_all("dt", recursive=False), "platform detail label"
        )
        value = _one(
            definition.find_all("dd", recursive=False), "platform detail value"
        )
        label = _text(heading)
        if label in labels:
            raise BusanDongguContractError("duplicate platform detail field")
        labels.append(label)
        if label in _PLATFORM_SAFE_LABELS:
            safe[label] = _text(value)
        else:
            skipped.add(label)
    required = tuple(
        label for label in labels if label not in _PLATFORM_OPTIONAL_LABELS
    )
    if required != _PLATFORM_DETAIL_REQUIRED:
        raise BusanDongguContractError("platform detail field order changed")
    return labels, safe, skipped


def _date_pair(value: Any, label: str) -> tuple[str, str]:
    dates = _lifelong._dates(value)
    if len(dates) != 2 or dates[1] < dates[0]:
        raise BusanDongguContractError(f"{label} changed")
    return dates[0].isoformat(), dates[1].isoformat()


def _parse_platform_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    identity = _clean(raw.get("identity"))
    if _exact_response_scope(
        final_url, _lifelong.BUSAN_LIFELONG_HOST, BUSAN_LIFELONG_DETAIL_PATH
    ) != {"lng_id": [identity]}:
        raise BusanDongguContractError("platform detail response identity changed")
    for name, expected in (
        ("lng_id", identity),
        ("inst_id", BUSAN_LIFELONG_DONGGU_OFFICE),
    ):
        fields = {
            _clean(node.get("value"))
            for node in soup.select(f"input[name='{name}']")
        }
        if fields != {expected}:
            raise BusanDongguContractError(f"platform detail {name} changed")
    heading = _one(soup.select("h2.enrolTit"), "platform detail title")
    prefix = _text(_one(heading.select(":scope > span"), "platform office prefix"))
    if prefix != f"[{BUSAN_LIFELONG_DONGGU_OFFICE_NAME}]":
        raise BusanDongguContractError("platform detail office changed")
    direct_title = _clean(
        " ".join(
            str(child)
            for child in heading.children
            if isinstance(child, NavigableString)
        )
    )
    if direct_title != _clean(parent.get("title")):
        raise BusanDongguContractError("platform list/detail title mismatch")
    labels, safe, skipped = _platform_detail_values(soup)
    if any(
        not safe.get(label)
        for label in _PLATFORM_SAFE_LABELS
        if label in labels
    ):
        raise BusanDongguContractError("platform safe detail value is empty")
    start, end = _date_pair(safe["교육기간"], "platform education period")
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ):
        raise BusanDongguContractError("platform list/detail dates mismatch")
    controls = soup.select("#learning_aply_btn")
    if len(controls) > 1:
        raise BusanDongguContractError("multiple platform application controls")
    control_label = _text(controls[0]) if controls else ""
    source_apply_status = safe["신청상태"]
    active = bool(
        len(controls) == 1
        and "접수중" in source_apply_status
        and _clean(controls[0].get("onclick"))
        == "fn_learning_apply(); return false;"
        and control_label in {"일반모집신청", "대기자신청", "우선모집신청"}
    )
    if controls and not active:
        raise BusanDongguContractError(
            "platform application control/status changed"
        )
    result = dict(parent)
    if active:
        status = "OPEN"
        application_type = (
            "WAITLIST_APPLY"
            if control_label == "대기자신청"
            else "ONLINE_RESERVATION"
        )
    else:
        status = (
            "SCHEDULED" if "접수대기" in source_apply_status else "CLOSED"
        )
        application_type = "INFO_ONLY"
    result.update(
        {
            "application_url": _clean(parent.get("raw_url")) if active else "",
            "application_type": application_type,
            "reservation_available": active,
            "status": status,
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
        "detail_source_status": source_apply_status,
        "contact_value_never_read": "문의전화" in skipped,
        "enrollment_value_never_read": "접수인원" in skipped,
        "instructor_value_never_read": "강사" in skipped,
        "attachments_never_read": {
            "강좌소개 첨부파일",
            "강의계획서",
        }.issubset(skipped),
        "free_form_values_never_read": {
            "강좌소개",
            "주의사항",
            "검색키워드",
            "강좌제한",
        }.issubset(skipped),
        "application_form_fetched": False,
        "applicant_list_fetched": False,
    }
    return result


def _city_list_contract(
    soup: BeautifulSoup, final_url: str, *, page: int
) -> tuple[int, Optional[Tag]]:
    if _exact_response_scope(
        final_url, BUSAN_CITY_HOST, BUSAN_CITY_LIST_PATH
    ) != {
        "curPage": [str(page)],
        "srchGugun": [BUSAN_CITY_DONGGU_GUGUN],
        "srchResveInsttCd": [BUSAN_CITY_RESIDENT_OFFICE],
    }:
        raise BusanDongguContractError("city list response query changed")
    if _text(_one(soup.select("title"), "city list title")) != _CITY_LIST_TITLE:
        raise BusanDongguContractError("city list title changed")
    form = _one(soup.select("form#srchForm[name='srchForm']"), "city search form")
    if (
        _clean(form.get("method")).casefold() != "get"
        or urlparse(_clean(form.get("action"))).path != "/lctre"
    ):
        raise BusanDongguContractError("city search form changed")
    page_field = _one(form.select("input[name='curPage']"), "city curPage")
    if _clean(page_field.get("value")) != str(page):
        raise BusanDongguContractError("city form page changed")
    for name, expected in (
        ("srchGugun", BUSAN_CITY_DONGGU_GUGUN),
        ("srchResveInsttCd", BUSAN_CITY_RESIDENT_OFFICE),
    ):
        selected = form.select(f"select[name='{name}'] > option[selected]")
        if len(selected) != 1 or _clean(selected[0].get("value")) != expected:
            raise BusanDongguContractError(f"city {name} filter changed")
    end_link = _one(
        soup.select("div.paginate > a.pgEnd[href]"), "city final page"
    )
    parsed_end = urlparse(
        urljoin(BUSAN_CITY_DONGGU_URL, _clean(end_link.get("href")))
    )
    end_query = parse_qs(parsed_end.query, keep_blank_values=True)
    if (
        (parsed_end.hostname or "").rstrip(".").lower() != BUSAN_CITY_HOST
        or parsed_end.path != BUSAN_CITY_LIST_PATH
        or set(end_query) != {"curPage", "srchGugun", "srchResveInsttCd"}
        or end_query.get("srchGugun") != [BUSAN_CITY_DONGGU_GUGUN]
        or end_query.get("srchResveInsttCd")
        != [BUSAN_CITY_RESIDENT_OFFICE]
        or len(end_query.get("curPage", [])) != 1
        or not end_query["curPage"][0].isdigit()
    ):
        raise BusanDongguContractError("unsafe city final-page control")
    last = int(end_query["curPage"][0])
    roots = soup.select("ul.reserveList")
    if page <= last:
        return last, _one(roots, "city reserve list")
    if page == last + 1:
        if roots:
            raise BusanDongguContractError("city sentinel is not empty")
        return last, None
    raise BusanDongguContractError("city request passed sentinel")


def _city_date_ranges(value: Any) -> tuple[str, str, str, str]:
    match = _CITY_DATES_RE.fullmatch(_clean(value))
    if not match:
        raise BusanDongguContractError("city card dates changed")
    parts = [date.fromisoformat(part).isoformat() for part in match.groups()]
    if parts[1] < parts[0] or parts[3] < parts[2]:
        raise BusanDongguContractError("city card date range is reversed")
    return parts[0], parts[1], parts[2], parts[3]


def _parse_city_page(
    soup: BeautifulSoup,
    final_url: str,
    *,
    page: int,
    expected_last: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int]:
    last, root = _city_list_contract(soup, final_url, page=page)
    if expected_last is not None and last != expected_last:
        raise BusanDongguContractError("city final page changed")
    rows: list[dict[str, Any]] = []
    items = root.find_all("li", recursive=False) if root else []
    for position, item in enumerate(items, 1):
        link = _one(
            item.select(":scope > a.reserveItem[onclick]"), "city course link"
        )
        action = _CITY_ACTION_RE.fullmatch(_clean(link.get("onclick")))
        if not action:
            raise BusanDongguContractError("city identity action changed")
        group_id, program_id = action.groups()
        identity = f"{group_id}:{program_id}"
        title_node = _one(link.select(":scope .tit"), "city course title")
        title = _text(title_node)
        if not title or _clean(title_node.get("title")) != title:
            raise BusanDongguContractError("city card title changed")
        source_status = _text(
            _one(link.select(":scope .statusMark"), "city status")
        )
        if source_status not in _CITY_STATUS_MAP:
            raise BusanDongguContractError("unknown city status")
        definitions = _one(link.select(":scope .infoBox > dl"), "city card values")
        headings = definitions.find_all("dt", recursive=False)
        values = definitions.find_all("dd", recursive=False)
        labels = tuple(_text(node) for node in headings)
        if labels != _CITY_CARD_LABELS or len(values) != len(labels):
            raise BusanDongguContractError("city card labels changed")
        # The final value is an inquiry phone and is intentionally never read.
        safe = {
            label: _text(value)
            for label, value in zip(labels[:-1], values[:-1])
        }
        if any(not value for value in safe.values()):
            raise BusanDongguContractError("city safe card value is empty")
        branch = safe["기관"]
        if not branch.startswith("동구 ") or not branch.endswith(" 주민자치회"):
            raise BusanDongguContractError("city course left Dong-gu owner")
        apply_start, apply_end, start, end = _city_date_ranges(safe["일자"])
        method = ", ".join(
            part
            for part in (_clean(part) for part in safe["방법"].split(","))
            if part
        )
        if not method:
            raise BusanDongguContractError("city application method is empty")
        rows.append(
            {
                "provider": BUSAN_DONGGU_PROVIDER,
                "provider_course_id": (
                    f"{BUSAN_DONGGU_PROVIDER}:reserve:{identity}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "description": title,
                "branch": branch,
                "branch_code": f"donggu-reserve-{group_id}",
                "preserve_branch": True,
                "category": "주민자치프로그램",
                "program_type": "교육/강좌",
                "raw_url": busan_donggu_city_detail_url(group_id, program_id),
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
                "provider_organizer": branch,
                "municipality_code": BUSAN_DONGGU_MUNICIPALITY_CODE,
                "municipality_name": BUSAN_DONGGU_MUNICIPALITY_NAME,
                "sido": "부산광역시",
                "sigungu": "동구",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "collection_type": (
                    "complete_html_pages+current_detail_allowlist"
                ),
                "raw_fields": {
                    "parser": BUSAN_DONGGU_PARSER,
                    "source_catalog": (
                        "busan_reserve_donggu_resident_councils"
                    ),
                    "source_identity": identity,
                    "source_group_id": group_id,
                    "source_program_id": program_id,
                    "source_page": page,
                    "source_position": position,
                    "source_status": source_status,
                    "source_application_method": method,
                    "source_card_dates": safe["일자"],
                    "inquiry_value_never_read": True,
                    "detail_verified": False,
                    "application_form_fetched": False,
                    "applicant_list_fetched": False,
                    "service_family": "education",
                },
            }
        )
    return rows, last


def _city_detail_values(
    info: Tag,
) -> tuple[list[str], dict[str, str], set[str]]:
    labels: list[str] = []
    safe: dict[str, str] = {}
    skipped: set[str] = set()
    for definition in info.find_all("dl", recursive=False):
        heading = _one(
            definition.find_all("dt", recursive=False), "city detail label"
        )
        value = _one(
            definition.find_all("dd", recursive=False), "city detail value"
        )
        label = _text(heading)
        if label in labels:
            raise BusanDongguContractError("duplicate city detail field")
        labels.append(label)
        if label in _CITY_DETAIL_SAFE_LABELS:
            safe[label] = _text(value)
        elif label in {"문의전화", "첨부파일"}:
            skipped.add(label)
        else:
            raise BusanDongguContractError(
                f"unknown city detail field {label!r}"
            )
    without_attachment = tuple(label for label in labels if label != "첨부파일")
    if without_attachment != _CITY_DETAIL_REQUIRED or "문의전화" not in skipped:
        raise BusanDongguContractError("city detail field order changed")
    return labels, safe, skipped


def _parse_city_detail(
    soup: BeautifulSoup, final_url: str, parent: Mapping[str, Any]
) -> dict[str, Any]:
    raw = dict(parent.get("raw_fields", {}))
    group_id = _clean(raw.get("source_group_id"))
    program_id = _clean(raw.get("source_program_id"))
    if _exact_response_scope(
        final_url, BUSAN_CITY_HOST, BUSAN_CITY_DETAIL_PATH
    ) != {"resveGroupSn": [group_id], "progrmSn": [program_id]}:
        raise BusanDongguContractError("city detail response identity changed")
    if _text(_one(soup.select("title"), "city detail title")) != _CITY_LIST_TITLE:
        raise BusanDongguContractError("city detail title changed")
    form = _one(soup.select("form#viewForm"), "city detail form")
    if _clean(form.get("method")).casefold() != "post" or _clean(
        form.get("action")
    ):
        raise BusanDongguContractError("city detail form changed")
    for name, expected in (("resveGroupSn", group_id), ("progrmSn", program_id)):
        field = _one(form.select(f":scope > input[name='{name}']"), f"city {name}")
        if _clean(field.get("value")) != expected:
            raise BusanDongguContractError("city detail identity changed")
    heading = _one(
        form.select(":scope > div.contHeader > h3.titPage"), "city title"
    )
    source_status = _text(
        _one(heading.select(":scope .statusMark"), "city status")
    )
    direct_title = _clean(
        " ".join(
            str(child)
            for child in heading.children
            if isinstance(child, NavigableString)
        )
    )
    if direct_title != _clean(parent.get("title")) or source_status != _clean(
        raw.get("source_status")
    ):
        raise BusanDongguContractError("city list/detail heading mismatch")
    info = _one(
        form.select(":scope > div.reserveStateWrap div.reserveStateInfo"),
        "city safe detail values",
    )
    _labels, safe, skipped = _city_detail_values(info)
    if any(not safe.get(label) for label in _CITY_DETAIL_SAFE_LABELS):
        raise BusanDongguContractError("city safe detail value is empty")
    if len(form.select(":scope > div.reserveDetail")) != 1:
        raise BusanDongguContractError("city free-form boundary changed")
    start, end = _date_pair(safe["운영기간"], "city operating period")
    apply_start, apply_end = _date_pair(
        safe["신청기간"], "city application period"
    )
    if (start, end) != (
        _clean(parent.get("start_date")),
        _clean(parent.get("end_date")),
    ) or (apply_start, apply_end) != (
        _clean(parent.get("apply_start")),
        _clean(parent.get("apply_end")),
    ):
        raise BusanDongguContractError("city list/detail dates mismatch")
    if (
        safe["신청방법"] != _clean(raw.get("source_application_method"))
        or safe["운영기관"] != _clean(parent.get("branch"))
        or safe["대상"] != _clean(parent.get("target"))
    ):
        raise BusanDongguContractError("city list/detail safe value mismatch")
    controls = form.select(
        ":scope > div.reserveStateWrap div.reserveBtnWrap > a.btnTypeXL"
    )
    if len(controls) > 1:
        raise BusanDongguContractError("multiple city application controls")
    control_label = _text(controls[0]) if controls else ""
    status = _CITY_STATUS_MAP[source_status]
    method = safe["신청방법"]
    active = False
    application_type = "INFO_ONLY"
    if status == "OPEN" and "온라인" in method:
        if len(controls) != 1 or not any(
            token in control_label for token in ("신청", "예약")
        ):
            raise BusanDongguContractError(
                "open online city course lacks control"
            )
        active = True
        application_type = "ONLINE_RESERVATION"
    elif status == "OPEN" and any(token in method for token in ("방문", "전화")):
        if control_label not in {"", "방문예약"}:
            raise BusanDongguContractError(
                "offline city application control changed"
            )
        application_type = "OFFLINE_APPLY"
    elif status == "CLOSED" and control_label not in {"", "접수마감"}:
        raise BusanDongguContractError("closed city control changed")
    elif status == "SCHEDULED" and control_label not in {
        "",
        "대기중",
        "접수대기",
    }:
        raise BusanDongguContractError("scheduled city control changed")
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
        "applicant_list_fetched": False,
    }
    return result


def _pii_key(value: Any) -> bool:
    lowered = _clean(value).casefold()
    if lowered.endswith(("value_never_read", "values_never_read")) or lowered in {
        "application_form_fetched",
        "applicant_list_fetched",
    }:
        return False
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
        if isinstance(value, str):
            cleaned = _clean(value)
            cleaned, phones = _PHONE_RE.subn("[redacted]", cleaned)
            cleaned, emails = _EMAIL_RE.subn("[redacted]", cleaned)
            redactions += phones + emails
            return cleaned
        return value

    return visit(row), redactions


def _row_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("provider_course_id")),
            _clean(row.get("title")),
            _clean(row.get("start_date")),
            _clean(row.get("end_date")),
            _clean(row.get("status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def collect_busan_donggu_education_courses(
    target: Any,
    timeout: int = 20,
    max_pages: int = 300,
    detail_limit: int = 250,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = BUSAN_DONGGU_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one fail-closed current/future snapshot of all three ledgers."""

    if not is_busan_donggu_target(target):
        return [], BUSAN_DONGGU_PARSER, _failure_meta(
            "target does not match the exact Busan Dong-gu integrated reservation route"
        )
    try:
        if any(
            isinstance(value, bool)
            for value in (timeout, max_pages, detail_limit, max_workers)
        ):
            raise ValueError("boolean limits are invalid")
        request_timeout = max(1, int(timeout))
        page_cap = max(0, int(max_pages))
        detail_cap = max(0, int(detail_limit))
        workers = min(max(1, int(max_workers)), BUSAN_DONGGU_MAX_WORKERS)
        cutoff = _today(today)
    except (TypeError, ValueError) as exc:
        return [], BUSAN_DONGGU_PARSER, _failure_meta(
            f"invalid limits/today: {_clean(exc)}", source_cap_reached=True
        )
    if page_cap < 1:
        return [], BUSAN_DONGGU_PARSER, _failure_meta(
            "max_pages cap cannot inspect all three ledgers",
            source_cap_reached=True,
        )

    district_rows, _district_parser, district_meta = (
        _collect_busan_donggu_district_courses(
            target,
            timeout=request_timeout,
            max_pages=page_cap,
            detail_limit=detail_cap,
            fetcher=fetcher,
            session_factory=session_factory,
            dedupe_rows=_dedupe_default,
            today=cutoff,
            max_workers=workers,
        )
    )
    district_ids = set(district_meta.pop("_district_source_identities", ()))
    if not district_meta.get("snapshot_complete"):
        district_meta["parser"] = BUSAN_DONGGU_PARSER
        return [], BUSAN_DONGGU_PARSER, district_meta
    if len(district_ids) != int(district_meta.get("unique_id_count") or 0):
        district_meta["snapshot_complete"] = False
        district_meta["configured_collection_error"] = (
            "district identity hand-off is incomplete"
        )
        return [], BUSAN_DONGGU_PARSER, district_meta

    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory
    source_sessions: list[Any] = []
    sessions_lock = threading.Lock()
    request_lock = threading.Lock()
    local = threading.local()
    additional_requests = 0

    def source_session() -> Any:
        value = getattr(local, "source_session", None)
        count = int(getattr(local, "source_request_count", 0))
        if value is None or count >= BUSAN_DONGGU_SESSION_REQUEST_LIMIT:
            if value is not None:
                _close_quietly(value)
            value = current_factory()
            local.source_session = value
            local.source_request_count = 0
            with sessions_lock:
                source_sessions.append(value)
        local.source_request_count = int(
            getattr(local, "source_request_count", 0)
        ) + 1
        return value

    def fetch_source(url: str) -> tuple[BeautifulSoup, str]:
        nonlocal additional_requests
        with request_lock:
            additional_requests += 1
        value = current_fetcher(source_session(), url, request_timeout)
        final_url = _clean(getattr(value, "url", "")) or url
        return _coerce_soup(value), final_url

    def fetch_batch(
        items: Sequence[
            tuple[Any, str, Callable[[BeautifulSoup, str], Any]]
        ],
    ) -> dict[Any, Any]:
        def run(
            item: tuple[Any, str, Callable[[BeautifulSoup, str], Any]]
        ) -> tuple[Any, Any]:
            key, url, parser = item
            soup, final_url = fetch_source(url)
            return key, parser(soup, final_url)

        values: dict[Any, Any] = {}
        with ThreadPoolExecutor(
            max_workers=min(workers, max(1, len(items))),
            thread_name_prefix="busan-donggu-source",
        ) as pool:
            for key, value in pool.map(run, items):
                values[key] = value
        return values

    platform_list_requests = 0
    city_list_requests = 0
    platform_sentinel_requests = 0
    city_sentinel_requests = 0
    platform_stability_rechecks = 0
    city_stability_rechecks = 0
    try:
        platform_censuses: list[list[dict[str, Any]]] = []
        platform_last = 0
        for census_index in range(2):
            soup, final_url = fetch_source(busan_donggu_lifelong_list_url(1))
            platform_list_requests += 1
            first_rows, current_last = _parse_platform_page(
                soup, final_url, page=1
            )
            if current_last > page_cap:
                raise BusanDongguContractError(
                    f"max_pages cap allows {page_cap} of {current_last} platform pages"
                )
            pages: dict[int, list[dict[str, Any]]] = {1: first_rows}
            for page in range(2, current_last + 1):
                soup, final_url = fetch_source(
                    busan_donggu_lifelong_list_url(page)
                )
                platform_list_requests += 1
                rows, _last = _parse_platform_page(
                    soup,
                    final_url,
                    page=page,
                    expected_last=current_last,
                )
                pages[page] = rows
            soup, final_url = fetch_source(
                busan_donggu_lifelong_list_url(current_last + 1)
            )
            platform_list_requests += 1
            platform_sentinel_requests += 1
            empty, sentinel_last = _parse_platform_page(
                soup,
                final_url,
                page=current_last + 1,
                expected_last=current_last,
            )
            if empty or sentinel_last != current_last:
                raise BusanDongguContractError("platform sentinel changed")
            census_rows = [
                row
                for page in range(1, current_last + 1)
                for row in pages[page]
            ]
            if census_index:
                platform_stability_rechecks += current_last + 1
            platform_last = current_last
            platform_censuses.append(census_rows)
        if _platform_semantic_multiset(
            platform_censuses[0]
        ) != _platform_semantic_multiset(platform_censuses[1]):
            raise BusanDongguContractError("platform complete censuses changed")
        platform_rows = platform_censuses[0]
        sequences = sorted(
            int(row["raw_fields"]["list_sequence"]) for row in platform_rows
        )
        if sequences != list(range(1, len(platform_rows) + 1)):
            raise BusanDongguContractError(
                "platform complete source sequence changed"
            )
        external_rows = [
            row
            for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "external"
        ]
        native_source_rows = [
            row
            for row in platform_rows
            if row.get("raw_fields", {}).get("identity_kind") == "internal"
        ]
        if len(external_rows) + len(native_source_rows) != len(platform_rows):
            raise BusanDongguContractError(
                "unexpected platform identity family"
            )
        external_datasids = [
            _platform_external_datasid(row) for row in external_rows
        ]
        if len(external_datasids) != len(set(external_datasids)):
            raise BusanDongguContractError(
                "repeated platform external data_Sid"
            )
        unmatched = sorted(set(external_datasids) - district_ids)
        if unmatched:
            raise BusanDongguContractError(
                "platform external data_Sid absent from district census: "
                + unmatched[0]
            )
        native_rows = [_platform_native_row(row) for row in native_source_rows]
        native_ids = [
            _clean(row.get("provider_course_id")) for row in native_rows
        ]
        if len(native_ids) != len(set(native_ids)):
            raise BusanDongguContractError(
                "duplicate native lifelong identity"
            )

        city_soup, city_final = fetch_source(busan_donggu_city_list_url(1))
        city_list_requests += 1
        city_first, city_last = _parse_city_page(
            city_soup, city_final, page=1
        )
        if city_last > page_cap:
            raise BusanDongguContractError(
                f"max_pages cap allows {page_cap} of {city_last} city pages"
            )
        city_pages: dict[int, list[dict[str, Any]]] = {1: city_first}
        for page in range(2, city_last + 1):
            soup, final_url = fetch_source(busan_donggu_city_list_url(page))
            city_list_requests += 1
            rows, _last = _parse_city_page(
                soup, final_url, page=page, expected_last=city_last
            )
            city_pages[page] = rows
        soup, final_url = fetch_source(
            busan_donggu_city_list_url(city_last + 1)
        )
        city_list_requests += 1
        city_sentinel_requests += 1
        city_empty, _last = _parse_city_page(
            soup,
            final_url,
            page=city_last + 1,
            expected_last=city_last,
        )
        if city_empty:
            raise BusanDongguContractError("city sentinel returned rows")
        recheck_pages = sorted({1, city_last})
        rechecked: dict[int, list[dict[str, Any]]] = {}
        for page in recheck_pages:
            soup, final_url = fetch_source(busan_donggu_city_list_url(page))
            city_list_requests += 1
            rows, _last = _parse_city_page(
                soup, final_url, page=page, expected_last=city_last
            )
            rechecked[page] = rows
        city_stability_rechecks = len(recheck_pages)
        for page in recheck_pages:
            if _row_signature(rechecked[page]) != _row_signature(
                city_pages[page]
            ):
                raise BusanDongguContractError("city boundary page changed")
        city_rows = [
            row
            for page in range(1, city_last + 1)
            for row in city_pages[page]
        ]
        city_ids = [
            _clean(row.get("provider_course_id")) for row in city_rows
        ]
        if len(city_ids) != len(set(city_ids)):
            raise BusanDongguContractError("duplicate city identity")

        native_current = [
            row
            for row in native_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        city_current = [
            row
            for row in city_rows
            if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
        ]
        current_count = len(district_rows) + len(native_current) + len(city_current)
        if current_count > detail_cap:
            raise BusanDongguContractError(
                f"detail_limit cap allows {detail_cap} of {current_count} current details"
            )
        detail_items: list[
            tuple[Any, str, Callable[[BeautifulSoup, str], dict[str, Any]]]
        ] = []
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
        enriched_by_id = fetch_batch(detail_items) if detail_items else {}
        additional_detail_rows = native_current + city_current
        enriched = list(district_rows) + [
            enriched_by_id[_clean(row["provider_course_id"])]
            for row in additional_detail_rows
        ]
        sanitized: list[dict[str, Any]] = []
        redactions = 0
        for row in enriched:
            safe_row, count = _sanitize_row(row)
            sanitized.append(safe_row)
            redactions += count
        deduper = dedupe_rows or _dedupe_default
        result = list(deduper(sanitized))
        before_ids = [
            _clean(row.get("provider_course_id")) for row in sanitized
        ]
        after_ids = [_clean(row.get("provider_course_id")) for row in result]
        if (
            len(result) != len(sanitized)
            or Counter(after_ids) != Counter(before_ids)
            or len(after_ids) != len(set(after_ids))
        ):
            raise BusanDongguContractError(
                "dedupe changed the complete identity set"
            )

        district_request_count = int(district_meta.get("request_count") or 0)
        district_detail_pages = int(district_meta.get("detail_pages") or 0)
        district_current = len(district_rows)
        source_total = (
            int(district_meta.get("total_count") or 0)
            + len(platform_rows)
            + len(city_rows)
        )
        district_meta.update(
            {
                "provider": BUSAN_DONGGU_PROVIDER,
                "candidate_id": BUSAN_DONGGU_CANDIDATE_ID,
                "canonical_url": BUSAN_DONGGU_URL,
                "city_canonical_url": BUSAN_CITY_DONGGU_URL,
                "lifelong_office_code": BUSAN_LIFELONG_DONGGU_OFFICE,
                "ownership_scope": BUSAN_DONGGU_OWNERSHIP_SCOPE,
                "parser": BUSAN_DONGGU_PARSER,
                "district_source_rows": int(
                    district_meta.get("total_count") or 0
                ),
                "district_data_pages": int(
                    district_meta.get("list_pages") or 0
                ),
                "district_current_count": district_current,
                "platform_source_rows": len(platform_rows),
                "platform_data_pages": platform_last,
                "platform_external_duplicate_rows": len(external_rows),
                "platform_external_unique_datasids": len(
                    set(external_datasids)
                ),
                "platform_external_matching_district": len(external_datasids),
                "platform_native_rows": len(native_rows),
                "platform_native_current_count": len(native_current),
                "city_source_rows": len(city_rows),
                "city_data_pages": city_last,
                "city_current_count": len(city_current),
                "city_expired_count": len(city_rows) - len(city_current),
                "source_total": source_total,
                "duplicate_source_rows": len(external_rows),
                "unique_education_source_rows": source_total
                - len(external_rows),
                "current_count": len(result),
                "current_source_count": len(result),
                "returned_count": len(result),
                "status_counts": dict(
                    Counter(_clean(row.get("status")) for row in result)
                ),
                "application_control_count": sum(
                    bool(row.get("reservation_available")) for row in result
                ),
                "branch_counts": dict(
                    Counter(_clean(row.get("branch")) for row in result)
                ),
                "pii_redaction_count": redactions,
                "platform_list_requests": platform_list_requests,
                "city_list_requests": city_list_requests,
                "required_list_requests": district_request_count
                - district_detail_pages
                + platform_list_requests
                + city_list_requests,
                "required_detail_requests": len(result),
                "detail_pages": len(result),
                "sentinel_requests": int(
                    district_meta.get("sentinel_requests") or 0
                )
                + platform_sentinel_requests
                + city_sentinel_requests,
                "stability_rechecks": int(
                    district_meta.get("page_one_rechecks") or 0
                )
                + platform_stability_rechecks
                + city_stability_rechecks,
                "network_requests": district_request_count
                + additional_requests,
                "request_count": district_request_count
                + additional_requests,
                "pages": district_request_count + additional_requests,
                "network_retry_count": 0,
                "pagination_detected": True,
                "pagination_complete": True,
                "pagination_exhausted": True,
                "details_complete": True,
                "snapshot_complete": True,
                "full_snapshot_validated": True,
                "source_cap_reached": False,
                "reservation_discovery_links": sum(
                    bool(row.get("application_url")) for row in result
                ),
                "no_current_data": not result,
                "no_current_reason": (
                    "all three complete Dong-gu ledgers have no current/future rows"
                    if not result
                    else ""
                ),
                "configured_collection_error": "",
            }
        )
        return result, BUSAN_DONGGU_PARSER, district_meta
    except Exception as exc:
        message = _clean(exc) or exc.__class__.__name__
        district_meta.update(
            {
                "parser": BUSAN_DONGGU_PARSER,
                "network_requests": int(
                    district_meta.get("request_count") or 0
                )
                + additional_requests,
                "request_count": int(
                    district_meta.get("request_count") or 0
                )
                + additional_requests,
                "platform_list_requests": platform_list_requests,
                "city_list_requests": city_list_requests,
                "details_complete": False,
                "snapshot_complete": False,
                "full_snapshot_validated": False,
                "source_cap_reached": "cap" in message,
                "returned_count": 0,
                "configured_collection_error": message,
            }
        )
        return [], BUSAN_DONGGU_PARSER, district_meta
    finally:
        for value in source_sessions:
            _close_quietly(value)


collect_busan_donggu_target = collect_busan_donggu_education_courses


__all__ = [
    "BUSAN_CITY_DONGGU_GUGUN",
    "BUSAN_CITY_DONGGU_URL",
    "BUSAN_CITY_HOST",
    "BUSAN_CITY_RESIDENT_OFFICE",
    "BUSAN_DONGGU_CATEGORIES",
    "BUSAN_DONGGU_CANDIDATE_ID",
    "BUSAN_DONGGU_HOST",
    "BUSAN_DONGGU_MAX_WORKERS",
    "BUSAN_DONGGU_MUNICIPALITY_CODE",
    "BUSAN_DONGGU_MUNICIPALITY_NAME",
    "BUSAN_DONGGU_OWNERSHIP_SCOPE",
    "BUSAN_DONGGU_PARSER",
    "BUSAN_DONGGU_PATH",
    "BUSAN_DONGGU_PROVIDER",
    "BUSAN_DONGGU_URL",
    "BUSAN_LIFELONG_DONGGU_OFFICE",
    "BUSAN_LIFELONG_DONGGU_OFFICE_NAME",
    "BUSAN_LIFELONG_PROVIDER",
    "BusanDongguContractError",
    "DongguCategory",
    "busan_donggu_application_url",
    "busan_donggu_city_detail_url",
    "busan_donggu_city_list_url",
    "busan_donggu_detail_url",
    "busan_donggu_lifelong_detail_url",
    "busan_donggu_lifelong_list_url",
    "busan_donggu_list_url",
    "canonical_busan_donggu_course_identity",
    "collect_busan_donggu_education_courses",
    "collect_busan_donggu_target",
    "is_busan_donggu_target",
    "is_target",
]
