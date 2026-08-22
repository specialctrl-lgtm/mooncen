"""Fail-closed collector for the Taebaek Lifelong Learning Center ledger.

The official owner exposes five course-type partitions.  The regular-course
URL is canonical; online, citizen-academy, special, and custom programmes are
secondary partitions of the same owner.  Every released snapshot walks all
advertised pages, requests the immediate empty post-last page, and rechecks
the first/final/sentinel boundaries before it is considered complete.

Only current or future course details are requested.  Application, login,
applicant, download, and attachment endpoints are never fetched.  Instructor
names, careers, contacts, plan files, free-text bodies, and applicant counts
are deliberately discarded.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


TAEBAEK_MUNICIPALITY_CODE = "5119000000"
TAEBAEK_MUNICIPALITY_NAME = "강원특별자치도 태백시"
TAEBAEK_PROVIDER = "MUNI_WWW_TAEBAEK_GO_KR_89A80ED6"
TAEBAEK_HOST = "www.taebaek.go.kr"
TAEBAEK_LIST_PATH = "/tblll/webSelectLctreManageList.do"
TAEBAEK_DETAIL_PATH = "/tblll/webSelectLctreManageView.do"
TAEBAEK_CANONICAL_URL = (
    f"https://{TAEBAEK_HOST}{TAEBAEK_LIST_PATH}?key=1632&lctreSe=LCTRESE01"
)
TAEBAEK_URL_SHA1 = "89A80ED682E366D2DD0FF00FD4070BF6DE1B330A"
TAEBAEK_URL_SHA256 = (
    "39EB9441035442D16E52D9EC4B39606587F489AB8DFABE6D72EA9B4140E4FE7D"
)
TAEBAEK_CANONICAL_CANDIDATE_ID = "MUNI_IR_39EB94410354"
TAEBAEK_CITY_HOME_PROVIDER = "MUNI_WWW_TAEBAEK_GO_KR_A3BDD256"
TAEBAEK_CITY_HOME_CANDIDATE_ID = "MUNI_IR_331B330A8F9D"
TAEBAEK_LIBRARY_PROVIDER = "MUNI_LIB_GWE_GO_KR_BF2CA306"
TAEBAEK_LIBRARY_CANDIDATE_ID = "MUNI_IR_834DD7130A98"

TAEBAEK_BRANCH = "태백시 평생학습관"
TAEBAEK_BRANCH_ADDRESS = (
    "강원특별자치도 태백시 태백로 1239 (황지동, 평생학습관)"
)
TAEBAEK_PAGE_SIZE = 10
TAEBAEK_RECOMMENDED_MAX_PAGES = 50
TAEBAEK_RECOMMENDED_DETAIL_LIMIT = 100
TAEBAEK_RECOMMENDED_MAX_WORKERS = 4
TAEBAEK_FETCH_ATTEMPTS = 2
TAEBAEK_MAX_HTML_BYTES = 3_000_000
TAEBAEK_PARSER = (
    "taebaek_lifelong_complete_five_course_partitions+advertised_total_pages+"
    "exact_empty_post_last_sentinels+stable_first_final_sentinel_rechecks+"
    "current_future_detail_binding+application_control_no_endpoint_fetch+"
    "instructor_contact_plan_attachment_free_text_exclusion"
)


@dataclass(frozen=True)
class TaebaekPartition:
    code: str
    name: str
    key: str
    course_type: str

    @property
    def url(self) -> str:
        return (
            f"https://{TAEBAEK_HOST}{TAEBAEK_LIST_PATH}?"
            f"{urlencode({'key': self.key, 'lctreSe': self.course_type})}"
        )


TAEBAEK_PARTITIONS: tuple[TaebaekPartition, ...] = (
    TaebaekPartition("regular", "정규강좌", "1632", "LCTRESE01"),
    TaebaekPartition("online", "비대면(온라인) 강좌", "1633", "LCTRESE02"),
    TaebaekPartition("academy", "태백 시민아카데미", "1634", "LCTRESE03"),
    TaebaekPartition("special", "특별강좌", "1635", "LCTRESE04"),
    TaebaekPartition("custom", "맞춤형 프로그램", "1636", "LCTRESE05"),
)
TAEBAEK_PARTITION_BY_CODE = {item.code: item for item in TAEBAEK_PARTITIONS}
TAEBAEK_PARTITION_BY_PAIR = {
    (item.key, item.course_type): item for item in TAEBAEK_PARTITIONS
}

TAEBAEK_SECONDARY_SOURCE_URLS = tuple(
    item.url for item in TAEBAEK_PARTITIONS if item.code != "regular"
)
TAEBAEK_CANDIDATE_AUDIT: Mapping[str, Mapping[str, Any]] = {
    TAEBAEK_CANONICAL_CANDIDATE_ID: {
        "provider": TAEBAEK_PROVIDER,
        "url": TAEBAEK_CANONICAL_URL,
        "decision": "promote_new_complete_official_lifelong_owner",
    },
    TAEBAEK_CITY_HOME_CANDIDATE_ID: {
        "provider": TAEBAEK_CITY_HOME_PROVIDER,
        "url": "https://www.taebaek.go.kr/www/index.do",
        "decision": "exclude_general_city_navigation_home_without_course_rows",
    },
    TAEBAEK_LIBRARY_CANDIDATE_ID: {
        "provider": TAEBAEK_LIBRARY_PROVIDER,
        "url": "https://lib.gwe.go.kr/tblib/main",
        "decision": "keep_separate_education_office_library_owner_disabled",
    },
}
TAEBAEK_OWNER_BOUNDARIES: Mapping[str, str] = {
    TAEBAEK_CANONICAL_URL: "canonical_city_lifelong_course_owner",
    **{
        item.url: "included_course_type_partition_of_canonical_owner"
        for item in TAEBAEK_PARTITIONS
        if item.code != "regular"
    },
    "https://www.taebaek.go.kr/www/index.do": (
        "city_navigation_home_without_course_identity_ledger"
    ),
    "https://lib.gwe.go.kr/tblib/main": (
        "separate_provincial_education_office_library_owner"
    ),
}
TAEBAEK_LIVE_AUDIT_BASELINE: Mapping[str, Any] = {
    "checked_on": "2026-08-05",
    "route_totals": {
        "regular": 38,
        "online": 0,
        "academy": 0,
        "special": 0,
        "custom": 0,
    },
    "route_pages": {
        "regular": 4,
        "online": 1,
        "academy": 1,
        "special": 1,
        "custom": 1,
    },
    "source_total": 38,
    "current_count": 38,
    "source_status_counts": {"접수예정": 38},
    "detail_pages": 38,
    "list_requests": 24,
    "application_endpoints_called": 0,
}


class TaebaekContractError(RuntimeError):
    """Raised when the audited Taebaek public-source contract changes."""


@dataclass(frozen=True)
class _Course:
    partition: TaebaekPartition
    page: int
    display_number: int
    identity: str
    title: str
    category: str
    source_status: str
    apply_start: date
    apply_end: date
    application_periods: tuple[tuple[date, date], ...]
    event_start: date
    event_end: date
    target: str
    capacity_total: int
    waitlist_capacity_total: Optional[int]
    raw_apply_period: str
    raw_event_period: str
    detail_url: str
    list_application_control: bool


@dataclass(frozen=True)
class _Page:
    partition: TaebaekPartition
    requested_page: int
    advertised_total: int
    advertised_last: int
    rows: tuple[_Course, ...]
    signature: str


@dataclass(frozen=True)
class _Detail:
    venue: str
    schedule: str
    target: str
    capacity_total: int
    application_control: bool
    application_method: str


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"^[1-9]\d*$")
_SUMMARY_RE = re.compile(
    r"^총\s*([0-9,]+)\s*건\s*\[\s*([0-9,]+)\s*/\s*([0-9,]+)\s*페이지\s*\]$"
)
_LIST_PERIOD_RE = re.compile(
    r"(?:(\d+)차)?접수\s*:\s*((?:\d{2}|\d{4})\.\d{2}\.\d{2})\s*~\s*"
    r"((?:\d{2}|\d{4})\.\d{2}\.\d{2})"
)
_EVENT_PERIOD_RE = re.compile(
    r"교육\s*:\s*((?:\d{2}|\d{4})\.\d{2}\.\d{2})\s*~\s*"
    r"((?:\d{2}|\d{4})\.\d{2}\.\d{2})"
)
_CAPACITY_RE = re.compile(
    r"^정원\s*:\s*([0-9,]+)\s*/\s*([0-9,]+)(?:\s*명)?"
    r"(?:\s+대기\s*:\s*([0-9,]+)\s*/\s*([0-9,]+)(?:\s*명)?)?$"
)
_LONG_DATE_RANGE_RE = re.compile(
    r"^(20\d{2})[-.]([01]\d)[-.]([0-3]\d)(?:\s+[0-2]\d:[0-5]\d)?\s*~\s*"
    r"(20\d{2})[-.]([01]\d)[-.]([0-3]\d)(?:\s+[0-2]\d:[0-5]\d)?$"
)
_PHONE_RE = re.compile(r"(?<!\d)0\d{1,3}[-\s)]?\d{3,4}[-\s]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RESIDENT_RE = re.compile(r"(?<!\d)\d{6}\s*[- ]\s*[1-4]\d{6}(?!\d)")
_LIST_CAPTION = (
    "번호, 분류, 교육명, 강사명, 접수기간/교육기간, 교육대상, 모집인원, "
    "접수상태, 신청하기 순으로 정보제공"
)
_DETAIL_CAPTION = (
    "강좌구분, 분류, 개요, 강사명, 계획서, 기관, 장소, 접수기간, 교육기간, "
    "교육시간, 교육요일, 모집인원, 문의전화, 접수방법, 교육대상 순으로 정보제공"
)
_DETAIL_LABELS = frozenset(
    {
        "강좌구분",
        "분류",
        "개요",
        "강사명",
        "경력",
        "계획서",
        "기관",
        "장소",
        "접수기간",
        "교육기간",
        "교육시간",
        "교육요일",
        "모집인원",
        "문의전화",
        "접수방법",
        "교육대상",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "instructor",
        "instructor_name",
        "teacher",
        "teacher_name",
        "contact",
        "phone",
        "email",
        "career",
        "description",
        "body",
        "attachments",
        "attachment_urls",
        "applicant_count",
        "source_html",
        "raw_html",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _target_value(target: Any, key: str) -> Any:
    return target.get(key) if isinstance(target, Mapping) else getattr(target, key, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _today(value: Optional[date | datetime | str]) -> date:
    if value is None:
        return datetime.now(ZoneInfo("Asia/Seoul")).date()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo("Asia/Seoul")).date()
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_clean(value))


def is_taebaek_education_target(target: Any) -> bool:
    if _provider(target) != TAEBAEK_PROVIDER:
        return False
    value = _clean(_target_value(target, "url"))
    if value != TAEBAEK_CANONICAL_URL:
        return False
    try:
        parsed = urlparse(value)
        return bool(
            parsed.scheme == "https"
            and parsed.hostname == TAEBAEK_HOST
            and parsed.port is None
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.path == TAEBAEK_LIST_PATH
            and parse_qs(parsed.query, keep_blank_values=True)
            == {"key": ["1632"], "lctreSe": ["LCTRESE01"]}
        )
    except ValueError:
        return False


is_target = is_taebaek_education_target


def taebaek_list_url(partition: TaebaekPartition, page: int) -> str:
    if partition not in TAEBAEK_PARTITIONS or page < 1:
        raise TaebaekContractError("invalid Taebaek partition/page")
    return (
        f"https://{TAEBAEK_HOST}{TAEBAEK_LIST_PATH}?"
        + urlencode(
            {
                "key": partition.key,
                "lctreSe": partition.course_type,
                "pageUnit": str(TAEBAEK_PAGE_SIZE),
                "searchCnd": "all",
                "pageIndex": str(page),
            }
        )
    )


def _guard_url(value: str) -> None:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != TAEBAEK_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise TaebaekContractError(f"unsafe Taebaek request URL: {value}")
    query = parse_qs(parsed.query, keep_blank_values=True)
    pair = (
        _clean(query.get("key", [""])[0]),
        _clean(query.get("lctreSe", [""])[0]),
    )
    if pair not in TAEBAEK_PARTITION_BY_PAIR:
        raise TaebaekContractError("request is outside the five audited partitions")
    if parsed.path == TAEBAEK_LIST_PATH:
        if set(query) != {"key", "lctreSe", "pageUnit", "searchCnd", "pageIndex"}:
            raise TaebaekContractError("list query contract changed")
        if (
            query["pageUnit"] != [str(TAEBAEK_PAGE_SIZE)]
            or query["searchCnd"] != ["all"]
            or len(query["pageIndex"]) != 1
            or not query["pageIndex"][0].isdigit()
            or int(query["pageIndex"][0]) < 1
        ):
            raise TaebaekContractError("unsafe list paging query")
        return
    if parsed.path == TAEBAEK_DETAIL_PATH:
        if set(query) != {
            "key",
            "lctreSe",
            "lctreNo",
            "pageUnit",
            "pageIndex",
            "searchCnd",
        }:
            raise TaebaekContractError("detail query contract changed")
        if (
            query["pageUnit"] != [str(TAEBAEK_PAGE_SIZE)]
            or query["searchCnd"] != ["all"]
            or len(query["lctreNo"]) != 1
            or not _IDENTITY_RE.fullmatch(query["lctreNo"][0])
            or len(query["pageIndex"]) != 1
            or not query["pageIndex"][0].isdigit()
            or int(query["pageIndex"][0]) < 1
        ):
            raise TaebaekContractError("unsafe detail identity/paging query")
        return
    raise TaebaekContractError(f"request path is outside read allowlist: {parsed.path}")


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": TAEBAEK_CANONICAL_URL,
        }
    )
    return current


def _default_fetcher(session: Any, url: str, timeout: int) -> Any:
    return session.get(url, timeout=timeout, allow_redirects=True)


def _coerce_soup(result: Any, requested_url: str) -> tuple[BeautifulSoup, int]:
    if isinstance(result, BeautifulSoup):
        return result, len(str(result).encode("utf-8"))
    if isinstance(result, bytes):
        if len(result) > TAEBAEK_MAX_HTML_BYTES:
            raise TaebaekContractError("Taebaek response exceeds byte cap")
        return BeautifulSoup(result, "html.parser"), len(result)
    if isinstance(result, str):
        size = len(result.encode("utf-8"))
        if size > TAEBAEK_MAX_HTML_BYTES:
            raise TaebaekContractError("Taebaek response exceeds byte cap")
        return BeautifulSoup(result, "html.parser"), size
    status = int(getattr(result, "status_code", 200))
    if status != 200:
        raise TaebaekContractError(f"HTTP {status} for {requested_url}")
    final_url = _clean(getattr(result, "url", requested_url)) or requested_url
    _guard_url(final_url)
    content = getattr(result, "content", b"")
    text = getattr(result, "text", "")
    size = len(content) if isinstance(content, bytes) and content else len(str(text).encode("utf-8"))
    if size > TAEBAEK_MAX_HTML_BYTES:
        raise TaebaekContractError("Taebaek response exceeds byte cap")
    return BeautifulSoup(str(text), "html.parser"), size


def _request_soup(
    session: Any,
    url: str,
    timeout: int,
    fetcher: Optional[Fetcher],
) -> tuple[BeautifulSoup, int]:
    _guard_url(url)
    current_fetcher = fetcher or _default_fetcher
    last_error: Optional[Exception] = None
    for attempt in range(1, TAEBAEK_FETCH_ATTEMPTS + 1):
        try:
            result = current_fetcher(session, url, timeout)
            soup, size = _coerce_soup(result, url)
            if size <= 0:
                raise TaebaekContractError("empty Taebaek response")
            return soup, attempt
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _short_date(value: str, field: str) -> date:
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise TaebaekContractError(f"invalid {field}: {value}")
    year = int(parts[0])
    if year < 100:
        year += 2000
    try:
        return date(year, int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise TaebaekContractError(f"invalid {field}: {value}") from exc


def _long_date_range(value: str, field: str) -> tuple[date, date]:
    match = _LONG_DATE_RANGE_RE.fullmatch(_clean(value))
    if match is None:
        raise TaebaekContractError(f"invalid detail {field}: {_clean(value)}")
    try:
        start = date(int(match[1]), int(match[2]), int(match[3]))
        end = date(int(match[4]), int(match[5]), int(match[6]))
    except ValueError as exc:
        raise TaebaekContractError(f"invalid detail {field}: {_clean(value)}") from exc
    if start > end:
        raise TaebaekContractError(f"reversed detail {field}")
    return start, end


def _safe_detail_url(
    href: Any,
    partition: TaebaekPartition,
    page: int,
) -> tuple[str, str]:
    value = urljoin(TAEBAEK_CANONICAL_URL, _clean(href))
    _guard_url(value)
    parsed = urlparse(value)
    if parsed.path != TAEBAEK_DETAIL_PATH:
        raise TaebaekContractError("row title does not link to an audited detail")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        query["key"] != [partition.key]
        or query["lctreSe"] != [partition.course_type]
        or query["pageIndex"] != [str(page)]
    ):
        raise TaebaekContractError("row/detail partition or page binding changed")
    return value, query["lctreNo"][0]


def _row_status(cell: Any) -> tuple[str, bool]:
    text = _clean(cell.get_text(" ", strip=True))
    if not text:
        raise TaebaekContractError("empty course status")
    controls = [
        item
        for item in cell.select("a, button, input[type=submit], input[type=button]")
        if _clean(item.get_text(" ", strip=True) or item.get("value"))
    ]
    if any(token in text for token in ("교육마감", "교육종료", "접수인원마감", "접수마감")):
        return text, False
    if "접수예정" in text:
        return text, False
    if "접수중" in text or "신청" in text:
        if not controls:
            raise TaebaekContractError("open source status has no application control")
        return text, True
    raise TaebaekContractError(f"unknown Taebaek source status: {text}")


def _parse_row(
    tr: Any,
    partition: TaebaekPartition,
    page: int,
    total: int,
    row_index: int,
) -> _Course:
    cells = tr.select(":scope > td")
    if len(cells) != 8:
        raise TaebaekContractError("course row column contract changed")
    number_text = _clean(cells[0].get_text(" ", strip=True))
    if not number_text.isdigit():
        raise TaebaekContractError("course display number is not numeric")
    display_number = int(number_text)
    expected_number = total - (page - 1) * TAEBAEK_PAGE_SIZE - row_index
    if display_number != expected_number:
        raise TaebaekContractError("course display ordering changed")
    links = cells[2].select(":scope > a[href]")
    if len(links) != 1:
        raise TaebaekContractError("course title/detail link contract changed")
    title = _clean(links[0].get_text(" ", strip=True))
    category = _clean(cells[1].get_text(" ", strip=True))
    if not title or not category:
        raise TaebaekContractError("course title/category is empty")
    detail_url, identity = _safe_detail_url(links[0].get("href"), partition, page)

    period_text = _clean(cells[4].get_text(" ", strip=True))
    application_periods = list(_LIST_PERIOD_RE.finditer(period_text))
    event_match = _EVENT_PERIOD_RE.search(period_text)
    if not application_periods or event_match is None:
        raise TaebaekContractError(f"course {identity}: list period contract changed")
    apply_starts = [_short_date(match[2], "apply start") for match in application_periods]
    apply_ends = [_short_date(match[3], "apply end") for match in application_periods]
    apply_start, apply_end = min(apply_starts), max(apply_ends)
    event_start = _short_date(event_match[1], "event start")
    event_end = _short_date(event_match[2], "event end")
    if (
        any(start > end for start, end in zip(apply_starts, apply_ends))
        or apply_start > apply_end
        or event_start > event_end
    ):
        raise TaebaekContractError(f"course {identity}: reversed source period")

    capacity_text = _clean(cells[6].get_text(" ", strip=True))
    capacity_match = _CAPACITY_RE.fullmatch(capacity_text)
    if capacity_match is None:
        raise TaebaekContractError(f"course {identity}: capacity contract changed")
    capacity_total = int(capacity_match[2].replace(",", ""))
    if capacity_total < 1:
        raise TaebaekContractError(f"course {identity}: invalid capacity")
    waitlist_capacity_total = (
        int(capacity_match[4].replace(",", "")) if capacity_match[4] else None
    )
    if waitlist_capacity_total is not None and waitlist_capacity_total < 1:
        raise TaebaekContractError(f"course {identity}: invalid waitlist capacity")
    source_status, list_control = _row_status(cells[7])
    return _Course(
        partition=partition,
        page=page,
        display_number=display_number,
        identity=identity,
        title=title,
        category=category,
        source_status=source_status,
        apply_start=apply_start,
        apply_end=apply_end,
        application_periods=tuple(zip(apply_starts, apply_ends)),
        event_start=event_start,
        event_end=event_end,
        target=_clean(cells[5].get_text(" ", strip=True)),
        capacity_total=capacity_total,
        waitlist_capacity_total=waitlist_capacity_total,
        raw_apply_period=" / ".join(
            _clean(match.group(0)) for match in application_periods
        ),
        raw_event_period=_clean(event_match.group(0)),
        detail_url=detail_url,
        list_application_control=list_control,
    )


def _page_digest(rows: Iterable[_Course]) -> str:
    payload = "\n".join(
        "\x1f".join(
            (
                row.partition.code,
                str(row.page),
                str(row.display_number),
                row.identity,
                row.title,
                row.category,
                row.source_status,
                row.apply_start.isoformat(),
                row.apply_end.isoformat(),
                "|".join(
                    f"{start.isoformat()}/{end.isoformat()}"
                    for start, end in row.application_periods
                ),
                row.event_start.isoformat(),
                row.event_end.isoformat(),
                row.target,
                str(row.capacity_total),
                str(row.waitlist_capacity_total or ""),
                str(row.list_application_control),
            )
        )
        for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_page(
    soup: BeautifulSoup,
    partition: TaebaekPartition,
    page: int,
    *,
    sentinel: bool = False,
) -> _Page:
    titles = soup.select("title")
    if len(titles) != 1 or partition.name not in _clean(titles[0].get_text(" ", strip=True)):
        raise TaebaekContractError(f"{partition.code}: page title contract changed")
    forms = [
        form
        for form in soup.select("form")
        if _clean(form.get("name")) == "lctreManageVOForm"
    ]
    if len(forms) != 1:
        raise TaebaekContractError(f"{partition.code}: list form contract changed")
    hidden = {
        _clean(item.get("name")): _clean(item.get("value"))
        for item in forms[0].select("input[type=hidden][name]")
    }
    if hidden != {"key": partition.key, "lctreSe": partition.course_type}:
        raise TaebaekContractError(f"{partition.code}: hidden partition binding changed")

    summaries = []
    for item in soup.select(".row .col-sm-24.small"):
        match = _SUMMARY_RE.fullmatch(_clean(item.get_text(" ", strip=True)))
        if match is not None:
            summaries.append(match)
    if len(summaries) != 1:
        raise TaebaekContractError(f"{partition.code}: page summary contract changed")
    total = int(summaries[0][1].replace(",", ""))
    current_page = int(summaries[0][2].replace(",", ""))
    advertised_last = int(summaries[0][3].replace(",", ""))
    if current_page != page or advertised_last != max(1, math.ceil(total / TAEBAEK_PAGE_SIZE)):
        raise TaebaekContractError(f"{partition.code}: advertised pagination changed")

    tables = []
    for table in soup.select("table"):
        caption = table.select_one("caption")
        if caption is not None and _clean(caption.get_text(" ", strip=True)) == _LIST_CAPTION:
            tables.append(table)
    if len(tables) != 1:
        raise TaebaekContractError(f"{partition.code}: course table contract changed")
    raw_rows = tables[0].select(":scope > tbody > tr")
    if sentinel:
        if raw_rows:
            raise TaebaekContractError(f"{partition.code}: sentinel contains rows")
        rows: tuple[_Course, ...] = ()
    else:
        rows = tuple(
            _parse_row(tr, partition, page, total, index)
            for index, tr in enumerate(raw_rows)
        )
        expected_count = (
            0
            if total == 0
            else min(TAEBAEK_PAGE_SIZE, total - (page - 1) * TAEBAEK_PAGE_SIZE)
        )
        if len(rows) != expected_count:
            raise TaebaekContractError(
                f"{partition.code}: page {page} row boundary changed"
            )
    return _Page(
        partition=partition,
        requested_page=page,
        advertised_total=total,
        advertised_last=advertised_last,
        rows=rows,
        signature=_page_digest(rows),
    )


def _detail_fields(table: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for heading in table.select("tbody th"):
        label = _clean(heading.get_text(" ", strip=True))
        value_cell = heading.find_next_sibling("td")
        if not label or value_cell is None or label in fields:
            raise TaebaekContractError("malformed or duplicate detail field")
        fields[label] = _clean(value_cell.get_text(" ", strip=True))
    if set(fields) != _DETAIL_LABELS:
        raise TaebaekContractError("detail field vocabulary changed")
    return fields


def _parse_detail(soup: BeautifulSoup, course: _Course) -> _Detail:
    title_nodes = soup.select(".education_title > h3.h0")
    if len(title_nodes) != 1:
        raise TaebaekContractError(f"course {course.identity}: detail title changed")
    state_nodes = title_nodes[0].select(":scope > span.education_state")
    if len(state_nodes) != 1:
        raise TaebaekContractError(
            f"course {course.identity}: detail state/title binding changed"
        )
    title_with_state = _clean(title_nodes[0].get_text(" ", strip=True))
    detail_state = _clean(state_nodes[0].get_text(" ", strip=True))
    if not detail_state or not title_with_state.startswith(detail_state):
        raise TaebaekContractError(
            f"course {course.identity}: detail state/title binding changed"
        )
    detail_title = _clean(title_with_state[len(detail_state) :])
    if detail_title != course.title:
        raise TaebaekContractError(
            f"course {course.identity}: list/detail title mismatch"
        )
    tables = []
    for table in soup.select("table"):
        caption = table.select_one("caption")
        if caption is not None and _clean(caption.get_text(" ", strip=True)) == _DETAIL_CAPTION:
            tables.append(table)
    if len(tables) != 1:
        raise TaebaekContractError(f"course {course.identity}: detail table changed")
    fields = _detail_fields(tables[0])
    if (
        fields["강좌구분"] != course.partition.name
        or fields["분류"] != course.category
        or fields["교육대상"] != course.target
    ):
        raise TaebaekContractError(f"course {course.identity}: list/detail identity mismatch")
    detail_apply = _long_date_range(fields["접수기간"], "application period")
    detail_event = _long_date_range(fields["교육기간"], "education period")
    if detail_apply not in course.application_periods or detail_event != (
        course.event_start,
        course.event_end,
    ):
        raise TaebaekContractError(f"course {course.identity}: list/detail period mismatch")
    capacity_match = _CAPACITY_RE.fullmatch(fields["모집인원"])
    detail_waitlist_total = (
        int(capacity_match[4].replace(",", ""))
        if capacity_match is not None and capacity_match[4]
        else None
    )
    if (
        capacity_match is None
        or int(capacity_match[2].replace(",", "")) != course.capacity_total
        or detail_waitlist_total != course.waitlist_capacity_total
    ):
        raise TaebaekContractError(f"course {course.identity}: list/detail capacity mismatch")

    table_links = tables[0].select("a[href]")
    for link in table_links:
        parsed = urlparse(urljoin(course.detail_url, _clean(link.get("href"))))
        if parsed.hostname not in {None, TAEBAEK_HOST} or not parsed.path.endswith("downloadAtchFile.do"):
            raise TaebaekContractError(f"course {course.identity}: unexpected detail-table link")
    buttons = []
    for item in tables[0].find_all_next(["a", "button", "input"], limit=8):
        text = _clean(item.get_text(" ", strip=True) or item.get("value"))
        if text == "목록":
            break
        if "신청" in text or "접수" in text:
            buttons.append(item)
    application_control = bool(buttons)
    if course.list_application_control != application_control:
        raise TaebaekContractError(
            f"course {course.identity}: list/detail application control mismatch"
        )
    return _Detail(
        venue=fields["장소"],
        schedule=" / ".join(
            value
            for value in (fields["교육시간"], fields["교육요일"])
            if value
        ),
        target=fields["교육대상"],
        capacity_total=course.capacity_total,
        application_control=application_control,
        application_method=fields["접수방법"],
    )


def _effective_status(course: _Course, cutoff: date) -> str:
    if cutoff < course.apply_start:
        return "SCHEDULED"
    if course.apply_start <= cutoff <= course.apply_end and course.list_application_control:
        return "OPEN"
    return "CLOSED"


def _branch_code(provider: str, branch: str) -> str:
    digest = hashlib.sha1(f"{provider}|{branch}".encode("utf-8")).hexdigest()[:12].upper()
    return f"{provider}:{digest}"


def _base_output(target: Any) -> dict[str, Any]:
    return {
        "source_group": _clean(_target_value(target, "source_group")) or "municipal_reservation",
        "collection_category": _clean(_target_value(target, "collection_category")) or "공공예약",
        "domain_category": _clean(_target_value(target, "domain_category")) or "교육·강좌",
        "service_group": "공공강좌",
        "service_group_policy": "locked",
        "municipality_code": TAEBAEK_MUNICIPALITY_CODE,
        "municipality_full_name": TAEBAEK_MUNICIPALITY_NAME,
    }


def _output(target: Any, course: _Course, detail: _Detail, cutoff: date) -> dict[str, Any]:
    status = _effective_status(course, cutoff)
    if status == "OPEN" and not detail.application_control:
        raise TaebaekContractError(f"course {course.identity}: OPEN without control")
    application_url = course.detail_url if status == "OPEN" else ""
    row: dict[str, Any] = {
        "provider": TAEBAEK_PROVIDER,
        "provider_course_id": f"{TAEBAEK_PROVIDER}:lecture:{course.identity}",
        "prefer_incoming_provider_course_id": True,
        "title": course.title,
        "branch": TAEBAEK_BRANCH,
        "branch_code": _branch_code(TAEBAEK_PROVIDER, TAEBAEK_BRANCH),
        "preserve_branch": True,
        "branch_url": TAEBAEK_CANONICAL_URL,
        "address": TAEBAEK_BRANCH_ADDRESS,
        "raw_url": course.detail_url,
        "application_url": application_url,
        "application_type": "ONLINE_APPLICATION" if application_url else "INFORMATION_ONLY",
        "application_method_raw": detail.application_method or "정보 제공",
        "reservation_available": bool(application_url),
        "status": status,
        "period": f"{course.event_start.isoformat()} ~ {course.event_end.isoformat()}",
        "start_date": course.event_start.isoformat(),
        "end_date": course.event_end.isoformat(),
        "apply_period": f"{course.apply_start.isoformat()} ~ {course.apply_end.isoformat()}",
        "apply_start_date": course.apply_start.isoformat(),
        "apply_end_date": course.apply_end.isoformat(),
        "schedule_raw": detail.schedule,
        "target": detail.target,
        "capacity": f"{detail.capacity_total}명",
        "capacity_total": detail.capacity_total,
        "venue_name": detail.venue or TAEBAEK_BRANCH,
        "room": detail.venue,
        "category": course.category,
        "collection_type": TAEBAEK_PARSER,
        "raw_fields": {
            "parser": TAEBAEK_PARSER,
            "source_identity": course.identity,
            "source_partition": course.partition.code,
            "source_partition_name": course.partition.name,
            "source_page": course.page,
            "source_status": course.source_status,
            "source_event_period": course.raw_event_period,
            "source_apply_period": course.raw_apply_period,
            "detail_verified": True,
            "application_control_present": detail.application_control,
            "application_endpoint_fetched": False,
            "attachment_endpoint_fetched": False,
            "discarded_detail_fields": ["강사명", "경력", "계획서", "문의전화"],
        },
    }
    row.update(_base_output(target))
    return row


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(row).intersection(_FORBIDDEN_KEYS):
        errors.append("forbidden top-level field")
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping) or set(raw).intersection(_FORBIDDEN_KEYS):
        errors.append("forbidden raw field")
    for key, value in row.items():
        if key in {"raw_url", "application_url", "branch_url"}:
            continue
        text = _clean(value)
        if _PHONE_RE.search(text) or _EMAIL_RE.search(text) or _RESIDENT_RE.search(text):
            errors.append(f"possible PII in {key}")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity and identity not in seen:
            seen.add(identity)
            output.append(row)
    return output


def _initial_meta() -> dict[str, Any]:
    return {
        "municipality_code": TAEBAEK_MUNICIPALITY_CODE,
        "municipality_full_name": TAEBAEK_MUNICIPALITY_NAME,
        "provider": TAEBAEK_PROVIDER,
        "canonical_url": TAEBAEK_CANONICAL_URL,
        "provider_url_sha1": TAEBAEK_URL_SHA1,
        "canonical_url_sha256": TAEBAEK_URL_SHA256,
        "canonical_candidate_id": TAEBAEK_CANONICAL_CANDIDATE_ID,
        "candidate_audit": {key: dict(value) for key, value in TAEBAEK_CANDIDATE_AUDIT.items()},
        "owner_boundaries": dict(TAEBAEK_OWNER_BOUNDARIES),
        "secondary_source_urls": list(TAEBAEK_SECONDARY_SOURCE_URLS),
        "parser": TAEBAEK_PARSER,
        "live_audit_baseline": dict(TAEBAEK_LIVE_AUDIT_BASELINE),
        "route_totals": {},
        "route_pages": {},
        "route_page_counts": {},
        "sentinel_pages": {},
        "sentinel_counts": {},
        "stable_rechecks": {
            f"{partition.code}_{boundary}": False
            for partition in TAEBAEK_PARTITIONS
            for boundary in ("first", "final", "sentinel")
        },
        "source_total": 0,
        "source_rows": 0,
        "current_source_count": 0,
        "expired_source_count": 0,
        "returned_count": 0,
        "source_status_counts": {},
        "source_partition_counts": {},
        "branch_counts": {},
        "category_counts": {},
        "status_counts": {},
        "data_pages": 0,
        "list_requests": 0,
        "detail_pages": 0,
        "logical_requests": 0,
        "physical_requests": 0,
        "request_retry_count": 0,
        "application_controls": 0,
        "application_endpoints_called": 0,
        "applicant_endpoints_called": 0,
        "attachment_endpoints_called": 0,
        "privacy_violations": 0,
        "no_current_data": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": "",
    }


def collect_taebaek_education(
    target: Any,
    *,
    timeout: int = 30,
    max_pages: int = TAEBAEK_RECOMMENDED_MAX_PAGES,
    detail_limit: int = TAEBAEK_RECOMMENDED_DETAIL_LIMIT,
    max_workers: int = TAEBAEK_RECOMMENDED_MAX_WORKERS,
    today: Optional[date | datetime | str] = None,
    session_factory: SessionFactory = _default_session_factory,
    fetcher: Optional[Fetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect a complete and stable Taebaek lifelong-course snapshot."""

    meta = _initial_meta()
    if not is_taebaek_education_target(target):
        meta["configured_collection_error"] = "target is outside canonical Taebaek owner"
        return [], TAEBAEK_PARSER, meta
    try:
        cutoff = _today(today)
        timeout_value = int(timeout)
        pages_cap = int(max_pages)
        details_cap = int(detail_limit)
        workers = int(max_workers)
        if (
            timeout_value < 1
            or pages_cap < 1
            or details_cap < 0
            or not 1 <= workers <= 16
            or any(isinstance(value, bool) for value in (timeout, max_pages, detail_limit, max_workers))
        ):
            raise ValueError("invalid collector limits")
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        return [], TAEBAEK_PARSER, meta

    main_session = session_factory()
    try:
        def request_page(partition: TaebaekPartition, page: int, *, sentinel: bool = False) -> _Page:
            soup, attempts = _request_soup(
                main_session,
                taebaek_list_url(partition, page),
                timeout_value,
                fetcher,
            )
            meta["list_requests"] += 1
            meta["logical_requests"] += 1
            meta["physical_requests"] += attempts
            return _parse_page(soup, partition, page, sentinel=sentinel)

        partition_pages: dict[str, list[_Page]] = {}
        sentinels: dict[str, _Page] = {}
        all_courses: list[_Course] = []
        aggregate_pages = 0
        for partition in TAEBAEK_PARTITIONS:
            first = request_page(partition, 1)
            aggregate_pages += first.advertised_last
            if aggregate_pages > pages_cap:
                meta["source_cap_reached"] = True
                raise TaebaekContractError(
                    f"aggregate advertised pages exceed max_pages {pages_cap}"
                )
            pages = [first]
            for page_number in range(2, first.advertised_last + 1):
                current = request_page(partition, page_number)
                if (
                    current.advertised_total != first.advertised_total
                    or current.advertised_last != first.advertised_last
                ):
                    raise TaebaekContractError(
                        f"{partition.code}: pagination changed during traversal"
                    )
                pages.append(current)
            rows = [row for page in pages for row in page.rows]
            if len(rows) != first.advertised_total:
                raise TaebaekContractError(f"{partition.code}: advertised total mismatch")
            identities = [row.identity for row in rows]
            if len(identities) != len(set(identities)):
                raise TaebaekContractError(f"{partition.code}: duplicate identity")

            sentinel_number = first.advertised_last + 1
            sentinel_page = request_page(partition, sentinel_number, sentinel=True)
            if (
                sentinel_page.advertised_total != first.advertised_total
                or sentinel_page.advertised_last != first.advertised_last
                or sentinel_page.rows
            ):
                raise TaebaekContractError(f"{partition.code}: invalid post-last sentinel")

            first_recheck = request_page(partition, 1)
            stable = (
                first_recheck.advertised_total,
                first_recheck.advertised_last,
                first_recheck.signature,
            ) == (first.advertised_total, first.advertised_last, first.signature)
            meta["stable_rechecks"][f"{partition.code}_first"] = stable
            if not stable:
                raise TaebaekContractError(f"{partition.code}: first page stability failed")
            if first.advertised_last == 1:
                meta["stable_rechecks"][f"{partition.code}_final"] = True
            else:
                last_recheck = request_page(partition, first.advertised_last)
                expected_last = pages[-1]
                stable = (
                    last_recheck.advertised_total,
                    last_recheck.advertised_last,
                    last_recheck.signature,
                ) == (
                    expected_last.advertised_total,
                    expected_last.advertised_last,
                    expected_last.signature,
                )
                meta["stable_rechecks"][f"{partition.code}_final"] = stable
                if not stable:
                    raise TaebaekContractError(f"{partition.code}: final page stability failed")
            sentinel_recheck = request_page(partition, sentinel_number, sentinel=True)
            stable = (
                sentinel_recheck.advertised_total,
                sentinel_recheck.advertised_last,
                sentinel_recheck.signature,
            ) == (
                sentinel_page.advertised_total,
                sentinel_page.advertised_last,
                sentinel_page.signature,
            )
            meta["stable_rechecks"][f"{partition.code}_sentinel"] = stable
            if not stable:
                raise TaebaekContractError(f"{partition.code}: sentinel stability failed")

            partition_pages[partition.code] = pages
            sentinels[partition.code] = sentinel_page
            all_courses.extend(rows)
            meta["route_totals"][partition.code] = first.advertised_total
            meta["route_pages"][partition.code] = first.advertised_last
            meta["route_page_counts"][partition.code] = [len(page.rows) for page in pages]
            meta["sentinel_pages"][partition.code] = sentinel_number
            meta["sentinel_counts"][partition.code] = 0

        identities = [course.identity for course in all_courses]
        if len(identities) != len(set(identities)):
            raise TaebaekContractError("course identity overlaps across type partitions")
        current = [course for course in all_courses if course.event_end >= cutoff]
        if len(current) > details_cap:
            meta["source_cap_reached"] = True
            raise TaebaekContractError(
                f"detail_limit {details_cap} below current count {len(current)}"
            )

        def fetch_detail(course: _Course) -> tuple[_Course, _Detail, int]:
            worker_session = session_factory()
            try:
                soup, attempts = _request_soup(
                    worker_session, course.detail_url, timeout_value, fetcher
                )
                return course, _parse_detail(soup, course), attempts
            finally:
                _close_quietly(worker_session)

        details: list[tuple[_Course, _Detail, int]] = []
        if current:
            if workers == 1:
                details = [fetch_detail(course) for course in current]
            else:
                with ThreadPoolExecutor(max_workers=min(workers, len(current))) as executor:
                    details = list(executor.map(fetch_detail, current))
        meta["detail_pages"] = len(details)
        meta["logical_requests"] += len(details)
        meta["physical_requests"] += sum(item[2] for item in details)
        rows = [_output(target, course, detail, cutoff) for course, detail, _ in details]
        expected_ids = {
            f"{TAEBAEK_PROVIDER}:lecture:{course.identity}" for course in current
        }
        rows.sort(key=lambda row: (str(row["start_date"]), str(row["provider_course_id"])))
        rows = list((dedupe_rows or _dedupe_default)(rows))
        if len(rows) != len(current) or {
            _clean(row.get("provider_course_id")) for row in rows
        } != expected_ids:
            raise TaebaekContractError("dedupe changed complete current identity set")
        privacy_errors = [error for row in rows for error in _privacy_errors(row)]
        if privacy_errors:
            raise TaebaekContractError("; ".join(privacy_errors[:5]))

        meta.update(
            {
                "cutoff": cutoff.isoformat(),
                "source_total": len(all_courses),
                "source_rows": len(all_courses),
                "current_source_count": len(current),
                "expired_source_count": len(all_courses) - len(current),
                "returned_count": len(rows),
                "source_status_counts": dict(
                    sorted(Counter(course.source_status for course in all_courses).items())
                ),
                "source_partition_counts": dict(
                    sorted(Counter(course.partition.code for course in all_courses).items())
                ),
                "branch_counts": {TAEBAEK_BRANCH: len(rows)} if rows else {},
                "category_counts": dict(
                    sorted(Counter(course.category for course in current).items())
                ),
                "status_counts": dict(sorted(Counter(str(row["status"]) for row in rows).items())),
                "data_pages": aggregate_pages,
                "application_controls": sum(
                    bool(row["raw_fields"]["application_control_present"])
                    for row in rows
                ),
                "privacy_violations": 0,
                "no_current_data": not rows,
                "pagination_complete": True,
                "details_complete": len(details) == len(current),
                "snapshot_complete": bool(
                    len(details) == len(current)
                    and all(meta["stable_rechecks"].values())
                ),
            }
        )
        meta["full_snapshot_validated"] = meta["snapshot_complete"]
        meta["request_retry_count"] = meta["physical_requests"] - meta["logical_requests"]
        return rows, TAEBAEK_PARSER, meta
    except Exception as exc:
        meta["configured_collection_error"] = f"{type(exc).__name__}: {_clean(exc)}"
        meta["snapshot_complete"] = False
        meta["full_snapshot_validated"] = False
        meta["request_retry_count"] = max(
            0, meta["physical_requests"] - meta["logical_requests"]
        )
        return [], TAEBAEK_PARSER, meta
    finally:
        _close_quietly(main_session)


collect = collect_taebaek_education


__all__ = [
    "TAEBAEK_BRANCH",
    "TAEBAEK_BRANCH_ADDRESS",
    "TAEBAEK_CANONICAL_CANDIDATE_ID",
    "TAEBAEK_CANONICAL_URL",
    "TAEBAEK_CANDIDATE_AUDIT",
    "TAEBAEK_LIVE_AUDIT_BASELINE",
    "TAEBAEK_MUNICIPALITY_CODE",
    "TAEBAEK_OWNER_BOUNDARIES",
    "TAEBAEK_PARSER",
    "TAEBAEK_PARTITIONS",
    "TAEBAEK_PROVIDER",
    "TaebaekContractError",
    "collect",
    "collect_taebaek_education",
    "is_taebaek_education_target",
    "is_target",
    "taebaek_list_url",
]
