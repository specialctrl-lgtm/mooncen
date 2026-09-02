"""Fail-closed collector for Asan's official lifelong-learning catalogues.

``life.asan.go.kr`` is a landing shell.  Its public enrolment shortcut opens
the institution directory on ``sugang.asan.go.kr``; it is not an independent
course database.  The directory currently exposes 26 institutions.  The
unfiltered learning archive also contains a hidden development office and a
``SPORT`` facility partition, both of which are deliberately excluded from
the education snapshot.

The canonical collector validates the complete global learning archive, the
authoritative institution directory, and the separate (non-overlapping)
online-video catalogue.  The institution directory may reorder entries by
current application status, so ownership is validated as an exact code/name
set.  It verifies advertised totals, every data page, an immediate empty
sentinel, and a stable page-one recheck.  Current/future education rows and
every online-video row are then verified against their detail pages.  A
course-bound public application control is required before an application URL
is emitted.

Contact numbers, instructor names, applicant data, free-form descriptions,
attachments, and source HTML are never persisted.  Any incomplete contract
returns an empty result.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import html
import math
import re
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


ASAN_PROVIDER = "MUNI_SUGANG_ASAN_GO_KR_FF504CD1"
ASAN_CANONICAL_CANDIDATE_ID = "MUNI_IR_165EE3DF5357"
ASAN_HOST = "sugang.asan.go.kr"
ASAN_LEARNING_LIST_PATH = "/ilms/learning/learningList.do"
ASAN_LEARNING_DETAIL_PATH = "/ilms/learning/learningDetail.do"
ASAN_OFFICE_LIST_PATH = "/ilms/learning/officeList.do"
ASAN_MEDIA_LIST_PATH = "/ilms/media/learningList.do"
ASAN_MEDIA_DETAIL_PATH = "/ilms/media/learningDetail.do"
ASAN_CANONICAL_URL = f"https://{ASAN_HOST}{ASAN_LEARNING_LIST_PATH}"
ASAN_OFFICE_URL = f"https://{ASAN_HOST}{ASAN_OFFICE_LIST_PATH}"
ASAN_MEDIA_URL = f"https://{ASAN_HOST}{ASAN_MEDIA_LIST_PATH}"
ASAN_LANDING_URL = "https://life.asan.go.kr/"
ASAN_PAGE_SIZE = 50
ASAN_OFFICE_PAGE_SIZE = 100
ASAN_MEDIA_PAGE_SIZE = 24
ASAN_MAX_WORKERS = 4
ASAN_MUNICIPALITY_CODE = "4420000000"
ASAN_MUNICIPALITY_NAME = "충청남도 아산시"
ASAN_PARSER = (
    "asan_office26_code_name_set+dynamic_status_order+global_learning_archive+"
    "exclude_hidden_dev_and_facility+trailing_duplicate_status_normalization+"
    "online_media_fanout+empty_sentinels+page1_rechecks+current_details+pii_allowlist"
)


@dataclass(frozen=True)
class AsanOffice:
    code: str
    name: str


# This is the exact code/name set published by the official institution
# directory and re-audited on 2026-07-29.  The page reorders offices as their
# application status changes.  A rename/addition/removal changes ownership and
# must be reviewed instead of silently altering the production snapshot.
ASAN_EXPECTED_OFFICES: tuple[AsanOffice, ...] = (
    AsanOffice("OFFICE_00002330", "아산시평생학습관"),
    AsanOffice("OFFICE_00002390", "아산시평생학습관(건강스포츠)"),
    AsanOffice("OFFICE_00002450", "아산시평생학습관(골목길배움터)"),
    AsanOffice("OFFICE_00002361", "선장면 평생학습센터"),
    AsanOffice("OFFICE_00002369", "신창면 평생학습센터"),
    AsanOffice("OFFICE_00002410", "아산시평생학습관(공유아카데미)"),
    AsanOffice("OFFICE_00002363", "탕정면 평생학습센터"),
    AsanOffice("OFFICE_00002430", "(재)아산문화재단"),
    AsanOffice("OFFICE_00002338", "도고면 평생학습센터"),
    AsanOffice("OFFICE_00002337", "둔포면 평생학습센터"),
    AsanOffice("OFFICE_00002334", "배방읍 평생학습센터(본원)"),
    AsanOffice("OFFICE_00002360", "배방읍 평생학습센터(신도시)"),
    AsanOffice("OFFICE_00002336", "송악면 평생학습센터"),
    AsanOffice("OFFICE_00002460", "아산시평생학습관(서부분관)"),
    AsanOffice("OFFICE_00002335", "염치읍 평생학습센터"),
    AsanOffice("OFFICE_00002367", "영인면 평생학습센터"),
    AsanOffice("OFFICE_00002362", "온양1동 평생학습센터"),
    AsanOffice("OFFICE_00002331", "온양2동 평생학습센터"),
    AsanOffice("OFFICE_00002366", "온양3동 평생학습센터"),
    AsanOffice("OFFICE_00002333", "온양4동 평생학습센터"),
    AsanOffice("OFFICE_00002332", "온양5동 평생학습센터"),
    AsanOffice("OFFICE_00002364", "온양6동 평생학습센터"),
    AsanOffice("OFFICE_00002368", "음봉면 평생학습센터"),
    AsanOffice("OFFICE_00002365", "인주면 평생학습센터"),
    AsanOffice("OFFICE_00002400", "좋은평생교육원"),
    AsanOffice("OFFICE_00002440", "탕정종합사회복지관"),
)
ASAN_OFFICE_BY_CODE = {office.code: office for office in ASAN_EXPECTED_OFFICES}
ASAN_OFFICE_BY_NAME = {office.name: office for office in ASAN_EXPECTED_OFFICES}

ASAN_HIDDEN_DEVELOPMENT_OFFICE = AsanOffice(
    "OFFICE_00002156", "아산평생학습관 개발센터"
)
ASAN_ALLOWED_LEARNING_TYPES = frozenset(
    {"오프라인 강좌", "체육 시설", "화상 강좌"}
)
ASAN_EXCLUDED_LEARNING_TYPES = frozenset({"체육 시설"})

# The landing page and institution directory are navigation/ownership aliases.
# The media list is a disjoint subcatalogue collected by the canonical provider.
ASAN_OWNERSHIP_ALIAS_URLS: tuple[str, ...] = (
    ASAN_LANDING_URL,
    ASAN_OFFICE_URL,
    ASAN_MEDIA_URL,
)
ASAN_ALIAS_PROVIDERS: tuple[str, ...] = (
    "MUNI_LIFE_ASAN_GO_KR_D1D9A3F0",
    "MUNI_SUGANG_ASAN_GO_KR_F2AC2958",
    "MUNI_SUGANG_ASAN_GO_KR_3EED40DF",
)
ASAN_EXCLUDED_NON_COURSE_URLS: tuple[str, ...] = (
    "https://www.asan.go.kr/",
    "https://www.asan.go.kr/main/",
    "https://adwar.asan.go.kr/develop/m_news/?m_mode=view&pds_no=2024040901111532442",
)


Fetcher = Callable[[Any, str, int], Any]
SessionFactory = Callable[[], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_IDENTITY_RE = re.compile(r"LEARNING_[A-Za-z0-9_-]+\Z")
_LEARNING_ONCLICK_RE = re.compile(
    r"fn_learning_detail\(\s*['\"](LEARNING_[A-Za-z0-9_-]+)['\"]\s*\)"
)
_MEDIA_ONCLICK_RE = re.compile(
    r"fn_detail\(\s*['\"](LEARNING_[A-Za-z0-9_-]+)['\"]\s*\)"
)
_OFFICE_ONCLICK_RE = re.compile(
    r"fn_learning_list\(\s*['\"](OFFICE_[A-Za-z0-9_-]+)['\"]\s*\)"
)
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2}|\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\d)"
)
_TOTAL_RE = re.compile(
    r"총\s*([0-9,]+)\s*건\s*\(\s*([0-9,]+)\s*/\s*([0-9,]+)페이지"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:0\d{1,2})[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_CURRENT_STATUS_MAP: Mapping[str, str] = {
    "접수중": "OPEN",
    "대기접수": "OPEN",
    "대기": "SCHEDULED",
    "접수대기": "SCHEDULED",
    "접수예정": "SCHEDULED",
    "마감": "CLOSED",
    "접수마감": "CLOSED",
    "교육중": "CLOSED",
    "교육완료": "CLOSED",
    "교육종료": "CLOSED",
    "취소": "CANCELLED",
    "폐강": "CANCELLED",
}
_APPLICATION_LABELS = frozenset(
    {
        "수강신청",
        "수강신청하기",
        "신청하기",
        "일반모집신청",
        "추가모집신청",
        "대기자신청",
        "대기접수",
    }
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _target_value(target: Any, name: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(name)
    return getattr(target, name, None)


def _provider(target: Any) -> str:
    return _clean(_target_value(target, "provider"))


def _target_url(target: Any) -> str:
    return _clean(_target_value(target, "url"))


def _today(value: Optional[date | datetime | str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        return date.fromisoformat(_clean(value))
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _exact_url(value: Any, path: str) -> bool:
    parsed = urlparse(_clean(value))
    return bool(
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == ASAN_HOST
        and parsed.port is None
        and parsed.path == path
        and not parsed.query
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


def is_asan_education_target(target: Any) -> bool:
    return _provider(target) == ASAN_PROVIDER and _exact_url(
        _target_url(target), ASAN_LEARNING_LIST_PATH
    )


def is_asan_ownership_alias_target(target: Any) -> bool:
    return _target_url(target) in ASAN_OWNERSHIP_ALIAS_URLS


def is_asan_excluded_non_course_target(target: Any) -> bool:
    return _target_url(target) in ASAN_EXCLUDED_NON_COURSE_URLS


is_target = is_asan_education_target


def asan_learning_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return ASAN_CANONICAL_URL + "?" + urlencode(
        {"pageIndex": int(raw), "pageUnit": ASAN_PAGE_SIZE}
    )


def asan_office_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return ASAN_OFFICE_URL + "?" + urlencode(
        {"pageIndex": int(raw), "office_pageUnit": ASAN_OFFICE_PAGE_SIZE}
    )


def asan_media_list_url(page: Any = 1) -> str:
    raw = _clean(page)
    if not raw.isdigit() or int(raw) < 1:
        return ""
    return ASAN_MEDIA_URL + "?" + urlencode(
        {
            "pageIndex": int(raw),
            "pageUnit": ASAN_MEDIA_PAGE_SIZE,
            "search_sort_order": "DESC",
        }
    )


def asan_learning_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{ASAN_HOST}{ASAN_LEARNING_DETAIL_PATH}?"
        + urlencode({"lng_id": value})
    )


def asan_media_detail_url(identity: Any) -> str:
    value = _clean(identity)
    if not _IDENTITY_RE.fullmatch(value):
        return ""
    return (
        f"https://{ASAN_HOST}{ASAN_MEDIA_DETAIL_PATH}?"
        + urlencode({"lng_id": value})
    )


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


def _default_fetcher(current: Any, url: str, timeout: int) -> Any:
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        try:
            response = current.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
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


def _coerce_soup(value: Any) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        if not value:
            raise ValueError("empty HTML response")
        return BeautifulSoup(value, "lxml")
    status = int(getattr(value, "status_code", 200))
    if status != 200:
        raise ValueError(f"unexpected HTTP status {status}")
    if getattr(value, "history", None):
        raise ValueError("HTTP redirects are not accepted")
    final_url = _clean(getattr(value, "url", ""))
    if final_url:
        parsed = urlparse(final_url)
        if (
            parsed.scheme.lower() != "https"
            or (parsed.hostname or "").rstrip(".").lower() != ASAN_HOST
            or parsed.username
            or parsed.password
        ):
            raise ValueError("unsafe final response URL")
    content = getattr(value, "content", None)
    if content is None:
        content = getattr(value, "text", None)
    if not content:
        raise ValueError("empty HTML response")
    return BeautifulSoup(content, "lxml")


def _fetch_many(
    items: list[tuple[Any, str]],
    *,
    fetcher: Fetcher,
    session_factory: SessionFactory,
    timeout: int,
    max_workers: int,
) -> tuple[dict[Any, BeautifulSoup], list[str]]:
    if not items:
        return {}, []
    workers = max(1, min(int(max_workers or 1), len(items)))
    chunks: list[list[tuple[Any, str]]] = [[] for _ in range(workers)]
    for index, item in enumerate(items):
        chunks[index % workers].append(item)

    def run(chunk: list[tuple[Any, str]]) -> tuple[dict[Any, BeautifulSoup], list[str]]:
        values: dict[Any, BeautifulSoup] = {}
        errors: list[str] = []
        current = session_factory()
        try:
            for key, url in chunk:
                try:
                    values[key] = _coerce_soup(fetcher(current, url, timeout))
                except Exception as exc:
                    errors.append(f"{key}: {type(exc).__name__}: {_clean(exc)}")
        finally:
            _close_quietly(current)
        return values, errors

    results: dict[Any, BeautifulSoup] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, chunk) for chunk in chunks if chunk]
        for future in as_completed(futures):
            values, current_errors = future.result()
            results.update(values)
            errors.extend(current_errors)
    return results, errors


def _dates(value: Any) -> list[date]:
    result: list[date] = []
    for year, month, day in _DATE_RE.findall(_clean(value)):
        full_year = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            result.append(date(full_year, int(month), int(day)))
        except ValueError:
            return []
    return result


def _total(soup: BeautifulSoup) -> tuple[int, int, int]:
    match = _TOTAL_RE.search(_clean(soup.get_text(" ", strip=True)))
    if not match:
        raise ValueError("declared total/page marker missing")
    total, current, last = (
        int(value.replace(",", "")) for value in match.groups()
    )
    if total < 0 or current < 1 or last < 1:
        raise ValueError("invalid declared total/page marker")
    return total, current, last


def _node_text_without(node: Any, selectors: tuple[str, ...]) -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "lxml")
    for selector in selectors:
        for child in clone.select(selector):
            child.extract()
    return _clean(clone.get_text(" ", strip=True))


def _parse_offices(soup: BeautifulSoup) -> tuple[list[AsanOffice], list[str]]:
    offices: list[AsanOffice] = []
    errors: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[onclick*='fn_learning_list']"):
        match = _OFFICE_ONCLICK_RE.search(_clean(link.get("onclick")))
        if not match:
            errors.append("malformed institution-list action")
            continue
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        name_nodes = link.select("strong")
        if len(name_nodes) != 1:
            errors.append(f"{code}: institution name container mismatch")
            continue
        name = _clean(name_nodes[0].get_text(" ", strip=True))
        if not name:
            errors.append(f"{code}: empty institution name")
            continue
        offices.append(AsanOffice(code, name))
    return offices, errors


def _offices_match_expected(offices: Iterable[AsanOffice], total: int) -> bool:
    current = list(offices)
    return bool(
        total == len(ASAN_EXPECTED_OFFICES)
        and len(current) == len(ASAN_EXPECTED_OFFICES)
        and {(office.code, office.name) for office in current}
        == {(office.code, office.name) for office in ASAN_EXPECTED_OFFICES}
    )


def _provider_course_id(identity: str, catalogue: str) -> str:
    token = identity if _IDENTITY_RE.fullmatch(identity) else hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:24]
    return f"{ASAN_PROVIDER}:{catalogue}:{token}"


def _parse_learning_page(
    soup: BeautifulSoup, *, page: int, cutoff: date
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    tables = [table for table in soup.select("table.lecture") if table.select("thead th")]
    if len(tables) != 1:
        return [], [f"learning page {page}: expected one course table"]
    table = tables[0]
    headings = [_clean(node.get_text(" ", strip=True)) for node in table.select("thead th")]
    required = ("번호", "강좌명", "강좌유형", "교육기간", "접수기간", "상태", "보기")
    if len(headings) != 7 or any(
        token not in headings[index] for index, token in enumerate(required)
    ):
        errors.append(f"learning page {page}: unexpected table headers")

    for source_row in table.select("tbody tr"):
        cells = source_row.select("td")
        title_link = source_row.select_one("td.subject a[onclick]")
        if title_link is None:
            empty = _clean(source_row.get_text(" ", strip=True))
            if not empty or ("등록" in empty and "없" in empty):
                continue
            errors.append(f"learning page {page}: non-course table row")
            continue
        if len(cells) != 7:
            errors.append(f"learning page {page}: course row is not seven columns")
            continue
        sequence_raw = _clean(cells[0].get_text(" ", strip=True)).replace(",", "")
        identity_match = _LEARNING_ONCLICK_RE.search(_clean(title_link.get("onclick")))
        title_node = title_link.select_one(".tit")
        office_node = title_link.select_one(".org")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        office_name = _clean(office_node.get_text(" ", strip=True) if office_node else "")
        course_type = _clean(cells[2].get_text(" ", strip=True))
        row_errors: list[str] = []
        if not sequence_raw.isdigit() or int(sequence_raw) < 1:
            row_errors.append("invalid source sequence")
        if identity_match is None:
            row_errors.append("missing stable learning identity")
            identity = ""
        else:
            identity = identity_match.group(1)
        if not title:
            row_errors.append("empty course title")
        if office_name not in ASAN_OFFICE_BY_NAME and office_name != ASAN_HIDDEN_DEVELOPMENT_OFFICE.name:
            row_errors.append(f"unknown source institution {office_name!r}")
        if course_type not in ASAN_ALLOWED_LEARNING_TYPES:
            row_errors.append(f"unknown learning type {course_type!r}")

        period_node = cells[3].select_one(".s_type.blue")
        period_raw = _node_text_without(period_node, ("em.hidden", "pre"))
        period_dates = _dates(period_raw)
        if len(period_dates) != 2:
            row_errors.append("education period is not exactly two dates")
            start = end = cutoff
            reversed_period = False
        else:
            source_start, source_end = period_dates
            reversed_period = source_end < source_start
            start, end = sorted(period_dates)
            if reversed_period and end >= cutoff:
                row_errors.append("current/future education period is reversed")

        schedule_node = cells[3].select_one("pre")
        schedule = _clean(
            schedule_node.get_text(" ", strip=True) if schedule_node else ""
        )
        apply_node = cells[4].select_one(".s_type.red3")
        apply_raw = _node_text_without(apply_node, ("em.hidden",))
        apply_dates = _dates(apply_raw)
        if apply_raw and len(apply_dates) not in (0, 2):
            row_errors.append("ambiguous application period")
        apply_start = apply_end = ""
        reversed_apply = False
        if len(apply_dates) == 2:
            source_apply_start, source_apply_end = apply_dates
            reversed_apply = source_apply_end < source_apply_start
            normalized_apply = sorted(apply_dates)
            apply_start = normalized_apply[0].isoformat()
            apply_end = normalized_apply[1].isoformat()
            if reversed_apply and end >= cutoff:
                row_errors.append("current/future application period is reversed")

        capacity_node = cells[4].select_one(".s_type.indigo3")
        capacity_text = _node_text_without(capacity_node, ("em.hidden",))
        capacity_match = re.search(r"([0-9,]+)\s*명", capacity_text)
        capacity = (
            f"{int(capacity_match.group(1).replace(',', ''))}명"
            if capacity_match
            else ""
        )
        status_nodes = cells[5].select(".s_btn")
        source_status_values = [
            _clean(node.get_text(" ", strip=True)) for node in status_nodes
        ]
        source_status_values = [value for value in source_status_values if value]
        trailing_duplicate_status = bool(
            len(source_status_values) == 3
            and source_status_values[-1] == source_status_values[-2]
        )
        status_values = (
            source_status_values[:-1]
            if trailing_duplicate_status
            else source_status_values
        )
        # During an active education period the source can publish two badges,
        # for example ``교육중`` plus the enrolment state ``마감``.  The last
        # badge is the actionable state.  Two audited facility rows render that
        # final badge twice; normalize only that exact trailing duplicate while
        # retaining the source sequence for audit evidence.
        if not status_values or len(status_values) > 2:
            row_errors.append("expected one or two source status badges")
        elif any(value not in _CURRENT_STATUS_MAP for value in status_values):
            row_errors.append("unknown source status badge")
        source_status = status_values[-1] if status_values else ""
        action = cells[6].select_one("a, button, input[type='submit'], input[type='button']")
        action_label = _clean(
            (action.get_text(" ", strip=True) if action is not None else "")
            or (action.get("value") if action is not None else "")
        )
        if action_label not in {"수강신청", "수강정보"}:
            row_errors.append(f"unknown list action {action_label!r}")

        if row_errors:
            errors.extend(
                f"learning page {page} row {sequence_raw or '?'}: {message}"
                for message in row_errors
            )
            continue
        office = ASAN_OFFICE_BY_NAME.get(office_name)
        excluded_development = office is None
        excluded_facility = course_type in ASAN_EXCLUDED_LEARNING_TYPES
        raw_url = asan_learning_detail_url(identity)
        rows.append(
            {
                "provider": ASAN_PROVIDER,
                "provider_course_id": _provider_course_id(identity, "learning"),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": office_name,
                "branch_code": office.code if office else ASAN_HIDDEN_DEVELOPMENT_OFFICE.code,
                "municipality_code": ASAN_MUNICIPALITY_CODE,
                "municipality_name": ASAN_MUNICIPALITY_NAME,
                "sido": "충청남도",
                "sigungu": "아산시",
                "provider_organizer": office_name,
                "venue_name": office_name,
                "category": "평생학습",
                "program_type": "강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": _CURRENT_STATUS_MAP.get(source_status, "CLOSED"),
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": (
                    f"{apply_start} ~ {apply_end}" if apply_start and apply_end else ""
                ),
                "apply_start": apply_start,
                "apply_end": apply_end,
                "schedule_raw": schedule,
                "fee": "별도 안내",
                "capacity": capacity,
                "target": "",
                "description": title,
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "identity": identity,
                    "catalogue": "learning",
                    "list_page": page,
                    "list_sequence": int(sequence_raw),
                    "source_office_code": (
                        office.code if office else ASAN_HIDDEN_DEVELOPMENT_OFFICE.code
                    ),
                    "source_office_name": office_name,
                    "source_learning_type": course_type,
                    "source_status": source_status,
                    "source_status_values": status_values,
                    "source_status_values_raw": source_status_values,
                    "trailing_duplicate_source_status": trailing_duplicate_status,
                    "list_action": action_label,
                    "list_application_control": action_label == "수강신청",
                    "excluded_development_office": excluded_development,
                    "excluded_facility_type": excluded_facility,
                    "source_reversed_education_period": reversed_period,
                    "source_reversed_application_period": reversed_apply,
                },
            }
        )
    return rows, errors


def _parse_media_page(
    soup: BeautifulSoup, *, page: int
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for link in soup.select("a[onclick*='fn_detail']"):
        match = _MEDIA_ONCLICK_RE.search(_clean(link.get("onclick")))
        if match is None:
            errors.append(f"media page {page}: malformed detail action")
            continue
        identity = match.group(1)
        title_node = link.select_one(".tit")
        period_node = link.select_one(".date")
        title = _clean(title_node.get_text(" ", strip=True) if title_node else "")
        period_raw = _clean(period_node.get_text(" ", strip=True) if period_node else "")
        if not title:
            errors.append(f"media page {page} {identity}: empty title")
            continue
        if not period_raw.startswith("교육"):
            errors.append(f"media page {page} {identity}: missing education marker")
            continue
        raw_url = asan_media_detail_url(identity)
        rows.append(
            {
                "provider": ASAN_PROVIDER,
                "provider_course_id": _provider_course_id(identity, "media"),
                "prefer_incoming_provider_course_id": True,
                "title": title,
                "branch": ASAN_EXPECTED_OFFICES[0].name,
                "branch_code": ASAN_EXPECTED_OFFICES[0].code,
                "municipality_code": ASAN_MUNICIPALITY_CODE,
                "municipality_name": ASAN_MUNICIPALITY_NAME,
                "sido": "충청남도",
                "sigungu": "아산시",
                "provider_organizer": ASAN_EXPECTED_OFFICES[0].name,
                "venue_name": ASAN_EXPECTED_OFFICES[0].name,
                "category": "온라인강좌",
                "program_type": "강좌",
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "status": "CLOSED",
                "period": "상시",
                "start_date": "",
                "end_date": "",
                "apply_period": "상시",
                "apply_start": "",
                "apply_end": "",
                "schedule_raw": "상시",
                "fee": "별도 안내",
                "capacity": "",
                "target": "",
                "description": title,
                "source_group": "municipal_reservation",
                "collection_category": "공공예약",
                "domain_category": "교육·강좌",
                "service_group": "공공강좌",
                "service_group_policy": "locked",
                "operator_type": "지자체/공공기관",
                "collection_type": "static_html+detail_html",
                "raw_fields": {
                    "identity": identity,
                    "catalogue": "media",
                    "list_page": page,
                    "source_period": period_raw,
                },
            }
        )
    return rows, errors


def _page_signature(rows: Iterable[Mapping[str, Any]]) -> str:
    values = [
        (
            _clean(row.get("raw_fields", {}).get("identity")),
            _clean(row.get("title")),
            _clean(row.get("branch")),
            _clean(row.get("period")),
            _clean(row.get("raw_fields", {}).get("source_status")),
        )
        for row in rows
    ]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def _detail_pairs(soup: BeautifulSoup) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for definition in soup.select("dl"):
        heading = definition.find("dt")
        value = definition.find("dd")
        if heading is None or value is None:
            continue
        key = _clean(heading.get_text(" ", strip=True))
        if key and key not in pairs:
            pairs[key] = _clean(value.get_text(" ", strip=True))
    return pairs


def _detail_title(soup: BeautifulSoup, *, media: bool) -> tuple[str, str]:
    heading = soup.select_one("p.tit" if media else "h2.enrolTit")
    if heading is None:
        return "", ""
    clone = BeautifulSoup(str(heading), "lxml")
    prefix_node = clone.select_one("span")
    prefix = _clean(prefix_node.get_text(" ", strip=True) if prefix_node else "")
    for span in clone.select("span"):
        span.extract()
    return prefix, _clean(clone.get_text(" ", strip=True))


def _application_control(soup: BeautifulSoup) -> tuple[bool, str, list[str]]:
    controls = soup.select("#learning_aply_btn")
    labels: list[str] = []
    errors: list[str] = []
    for control in controls:
        label = _clean(
            control.get_text(" ", strip=True)
            or control.get("value")
            or control.get("title")
        )
        if label not in _APPLICATION_LABELS:
            errors.append(f"unknown detail application label {label!r}")
        if "fn_learning_apply" not in _clean(control.get("onclick")):
            errors.append("application control is not course-bound")
        labels.append(label)
    if labels and len(set(labels)) != 1:
        errors.append("conflicting duplicate application controls")
    return bool(controls and not errors), (labels[0] if labels else ""), errors


def _capacity(value: Any) -> str:
    text = _clean(value)
    match = re.search(r"총모집인원\s*([0-9,]+)\s*명", text)
    if not match:
        match = re.search(r"([0-9,]+)\s*명", text)
    if match:
        return f"{int(match.group(1).replace(',', ''))}명"
    return "상시" if "상시" in text else ""


def _validate_learning_detail(row: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    office_code = _clean(row.get("raw_fields", {}).get("source_office_code"))
    office_name = _clean(row.get("raw_fields", {}).get("source_office_name"))
    errors: list[str] = []
    identities = {
        _clean(node.get("value")) for node in soup.select("input[name='lng_id']")
    }
    if identities != {identity}:
        errors.append(f"{identity}: detail identity mismatch")
    offices = {
        _clean(node.get("value")) for node in soup.select("input[name='inst_id']")
    }
    if offices != {office_code}:
        errors.append(f"{identity}: detail institution mismatch")
    names = {
        _clean(node.get("value")) for node in soup.select("input[name='lng_nm']")
    }
    if names != {_clean(row.get("title"))}:
        errors.append(f"{identity}: hidden detail title mismatch")
    prefix, title = _detail_title(soup, media=False)
    if prefix != f"[{office_name}]" or title != _clean(row.get("title")):
        errors.append(f"{identity}: visible detail title/institution mismatch")

    pairs = _detail_pairs(soup)
    detail_period = _dates(pairs.get("교육기간"))
    expected_period = [
        date.fromisoformat(_clean(row.get("start_date"))),
        date.fromisoformat(_clean(row.get("end_date"))),
    ]
    if detail_period != expected_period:
        errors.append(f"{identity}: detail/list education period mismatch")
    hidden_start = {
        _clean(node.get("value"))
        for node in soup.select("input[name='alife_edu_bgng_ymd']")
    }
    hidden_end = {
        _clean(node.get("value"))
        for node in soup.select("input[name='alife_edu_end_ymd']")
    }
    if hidden_start != {_clean(row.get("start_date"))} or hidden_end != {
        _clean(row.get("end_date"))
    }:
        errors.append(f"{identity}: hidden detail period mismatch")

    control, control_label, control_errors = _application_control(soup)
    errors.extend(f"{identity}: {message}" for message in control_errors)
    list_control = bool(row.get("raw_fields", {}).get("list_application_control"))
    if control != list_control:
        errors.append(f"{identity}: list/detail application-control mismatch")
    source_status = _clean(row.get("raw_fields", {}).get("source_status"))
    if source_status not in _CURRENT_STATUS_MAP:
        errors.append(f"{identity}: unknown current source status {source_status!r}")
    if control and not errors:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = (
            "WAITLIST_APPLY" if "대기" in control_label else "ONLINE_RESERVATION"
        )
        row["reservation_available"] = True
        row["status"] = "OPEN"
    else:
        row["application_url"] = ""
        row["application_type"] = "INFO_ONLY"
        row["reservation_available"] = False
        row["status"] = _CURRENT_STATUS_MAP.get(source_status, "CLOSED")

    category = _clean(pairs.get("강좌분류"))
    target = _clean(pairs.get("교육대상"))
    venue = _clean(pairs.get("교육장소"))
    fee = _clean(pairs.get("수강료"))
    capacity = _capacity(pairs.get("접수인원"))
    if not all((category, target, venue, fee, capacity)):
        errors.append(f"{identity}: required allowlisted detail field missing")
    else:
        row["category"] = category
        row["target"] = target
        row["venue_name"] = venue
        row["fee"] = fee
        row["capacity"] = capacity
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_verified": not errors,
        "detail_application_control": control,
        "detail_application_label": control_label,
    }
    return errors


def _validate_media_detail(
    row: dict[str, Any], soup: BeautifulSoup, *, cutoff: date
) -> tuple[bool, list[str]]:
    identity = _clean(row.get("raw_fields", {}).get("identity"))
    errors: list[str] = []
    identities = {
        _clean(node.get("value")) for node in soup.select("input[name='lng_id']")
    }
    if identities != {identity}:
        errors.append(f"{identity}: media detail identity mismatch")
    names = {
        _clean(node.get("value")) for node in soup.select("input[name='lng_nm']")
    }
    if names != {_clean(row.get("title"))}:
        errors.append(f"{identity}: media hidden title mismatch")
    prefix, title = _detail_title(soup, media=True)
    expected_office = ASAN_EXPECTED_OFFICES[0].name
    if prefix != f"[{expected_office}]" or title != _clean(row.get("title")):
        errors.append(f"{identity}: media visible title/institution mismatch")

    pairs = _detail_pairs(soup)
    period_raw = _clean(pairs.get("교육기간"))
    period_dates = _dates(period_raw)
    education_status = _clean(pairs.get("교육상태"))
    if period_raw == "상시":
        current = education_status in {"교육중", "교육예정"}
        row["period"] = "상시"
        row["schedule_raw"] = _clean(pairs.get("교육시간")) or "상시"
        hidden_start = next(
            (
                _clean(node.get("value"))
                for node in soup.select("input[name='alife_edu_bgng_ymd']")
                if _clean(node.get("value"))
            ),
            "",
        )
        if hidden_start:
            try:
                date.fromisoformat(hidden_start)
            except ValueError:
                errors.append(f"{identity}: invalid media hidden start date")
            else:
                row["start_date"] = hidden_start
    elif len(period_dates) == 2:
        start, end = sorted(period_dates)
        current = end >= cutoff and education_status not in {"교육완료", "교육종료"}
        row["period"] = f"{start.isoformat()} ~ {end.isoformat()}"
        row["start_date"] = start.isoformat()
        row["end_date"] = end.isoformat()
    else:
        current = False
        errors.append(f"{identity}: invalid media education period")
    if education_status not in {"교육중", "교육예정", "교육완료", "교육종료"}:
        errors.append(f"{identity}: unknown media education status {education_status!r}")

    control, control_label, control_errors = _application_control(soup)
    errors.extend(f"{identity}: {message}" for message in control_errors)
    if current and control and not errors:
        row["application_url"] = _clean(row.get("raw_url"))
        row["application_type"] = (
            "WAITLIST_APPLY" if "대기" in control_label else "ONLINE_RESERVATION"
        )
        row["reservation_available"] = True
        row["status"] = "OPEN"
    elif current:
        row["status"] = "SCHEDULED" if education_status == "교육예정" else "CLOSED"

    category = _clean(pairs.get("강좌분류"))
    target = _clean(pairs.get("교육대상"))
    venue_pair = _clean(pairs.get("교육기관/교육장소"))
    venue = _clean(venue_pair.split("/", 1)[-1]) if venue_pair else ""
    fee = _clean(pairs.get("수강료"))
    capacity = _capacity(pairs.get("모집정원"))
    if not all((category, target, venue, fee, capacity)):
        errors.append(f"{identity}: required media allowlisted detail field missing")
    else:
        row["category"] = category
        row["target"] = target
        row["venue_name"] = venue
        row["fee"] = fee
        row["capacity"] = capacity
    row["raw_fields"] = {
        **row["raw_fields"],
        "detail_verified": not errors,
        "detail_application_control": control,
        "detail_application_label": control_label,
        "source_education_status": education_status,
    }
    return current, errors


def _contains_pii(value: Any) -> bool:
    text = _clean(value)
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text))


def _privacy_errors(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_strings = (
        "title",
        "branch",
        "provider_organizer",
        "venue_name",
        "category",
        "target",
        "description",
    )
    for key in allowed_strings:
        if _contains_pii(row.get(key)):
            errors.append(f"{_clean(row.get('provider_course_id'))}: PII in {key}")
    raw_fields = row.get("raw_fields") or {}
    forbidden_key_parts = (
        "phone",
        "tel",
        "email",
        "contact",
        "instructor",
        "teacher",
        "강사",
        "전화",
        "description",
        "body",
        "html",
        "attachment",
    )
    for key, value in raw_fields.items():
        normalized = _clean(key).casefold()
        if any(part in normalized for part in forbidden_key_parts):
            errors.append(f"{_clean(row.get('provider_course_id'))}: forbidden raw field {key}")
        if _contains_pii(value):
            errors.append(f"{_clean(row.get('provider_course_id'))}: PII in raw_fields")
    return errors


def _dedupe_default(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        identity = _clean(row.get("provider_course_id"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _base_meta() -> dict[str, Any]:
    return {
        "pages": 0,
        "list_requests": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "detail_errors": 0,
        "source_total": 0,
        "source_rows": 0,
        "current_count": 0,
        "returned_count": 0,
        "office_count": 0,
        "expected_office_count": len(ASAN_EXPECTED_OFFICES),
        "media_source_total": 0,
        "pagination_detected": False,
        "pagination_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "source_cap_reached": False,
        "no_current_data": False,
        "configured_collection_error": "",
        "municipality_code": ASAN_MUNICIPALITY_CODE,
        "municipality_name": ASAN_MUNICIPALITY_NAME,
        "canonical_candidate_id": ASAN_CANONICAL_CANDIDATE_ID,
        "canonical_url": ASAN_CANONICAL_URL,
        "ownership_alias_urls": list(ASAN_OWNERSHIP_ALIAS_URLS),
        "superseded_providers": list(ASAN_ALIAS_PROVIDERS),
        "excluded_non_course_urls": list(ASAN_EXCLUDED_NON_COURSE_URLS),
    }


def collect_asan_courses(
    target: Any,
    timeout: int = 30,
    max_pages: int = 200,
    detail_limit: int = 1000,
    *,
    fetcher: Optional[Fetcher] = None,
    session_factory: Optional[SessionFactory] = None,
    dedupe_rows: Optional[DedupeRows] = None,
    today: Optional[date | datetime | str] = None,
    max_workers: int = ASAN_MAX_WORKERS,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    meta = _base_meta()
    errors: list[str] = []
    if not is_asan_education_target(target):
        meta["configured_collection_error"] = "target is not the exact Asan canonical URL/provider"
        return [], ASAN_PARSER, meta
    cutoff = _today(today)
    request_timeout = max(1, int(timeout or 30))
    allowed_pages = max(0, int(max_pages or 0))
    allowed_details = max(0, int(detail_limit or 0))
    workers = max(1, min(int(max_workers or 1), ASAN_MAX_WORKERS))
    current_fetcher = fetcher or _default_fetcher
    current_factory = session_factory or _default_session_factory

    # Bootstrap the three catalogues before deciding the exact page budget.
    bootstrap_items = [
        (("office", 1, "data"), asan_office_list_url(1)),
        (("learning", 1, "data"), asan_learning_list_url(1)),
        (("media", 1, "data"), asan_media_list_url(1)),
    ]
    fetched, fetch_errors = _fetch_many(
        bootstrap_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=request_timeout,
        max_workers=workers,
    )
    errors.extend(fetch_errors)
    meta["pages"] += len(fetched)
    meta["list_requests"] += len(fetched)
    if errors or len(fetched) != len(bootstrap_items):
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], ASAN_PARSER, meta

    office_soup = fetched[("office", 1, "data")]
    learning_soup = fetched[("learning", 1, "data")]
    media_soup = fetched[("media", 1, "data")]
    try:
        office_total, office_current, office_last = _total(office_soup)
        learning_total, learning_current, learning_last = _total(learning_soup)
        media_total, media_current, media_last = _total(media_soup)
    except ValueError as exc:
        errors.append(_clean(exc))
        meta["configured_collection_error"] = "; ".join(dict.fromkeys(errors))
        return [], ASAN_PARSER, meta
    if office_current != 1 or learning_current != 1 or media_current != 1:
        errors.append("bootstrap response is not page one")
    expected_learning_last = max(1, math.ceil(learning_total / ASAN_PAGE_SIZE))
    expected_office_last = max(1, math.ceil(office_total / ASAN_OFFICE_PAGE_SIZE))
    expected_media_last = max(1, math.ceil(media_total / ASAN_MEDIA_PAGE_SIZE))
    if learning_last != expected_learning_last:
        errors.append("learning advertised last page mismatch")
    if office_last != expected_office_last:
        errors.append("office advertised last page mismatch")
    if media_last != expected_media_last:
        errors.append("media advertised last page mismatch")
    if office_last != 1:
        errors.append("institution directory unexpectedly exceeds one audited page")

    offices, office_errors = _parse_offices(office_soup)
    errors.extend(office_errors)
    if not _offices_match_expected(offices, office_total):
        errors.append("official institution directory changed")
    meta["office_count"] = len(offices)

    first_learning_rows, first_learning_errors = _parse_learning_page(
        learning_soup, page=1, cutoff=cutoff
    )
    first_media_rows, first_media_errors = _parse_media_page(media_soup, page=1)
    errors.extend(first_learning_errors)
    errors.extend(first_media_errors)

    required_page_requests = (
        3  # bootstrap page one for each catalogue
        + 1  # office immediate sentinel
        + 1  # office page-one recheck
        + (learning_last - 1)
        + 1  # learning immediate sentinel
        + 1  # learning page-one recheck
        + (media_last - 1)
        + 1  # media immediate sentinel
        + 1  # media page-one recheck
    )
    meta["required_page_requests"] = required_page_requests
    if required_page_requests > allowed_pages:
        meta["source_cap_reached"] = True
        errors.append(
            f"max_pages cap allows {allowed_pages} of {required_page_requests} required source requests"
        )
    if errors:
        meta.update(
            {
                "source_total": learning_total,
                "media_source_total": media_total,
                "declared_learning_pages": learning_last,
                "declared_media_pages": media_last,
                "configured_collection_error": "; ".join(dict.fromkeys(errors)),
            }
        )
        return [], ASAN_PARSER, meta

    remaining_items: list[tuple[Any, str]] = [
        (("office", 2, "sentinel"), asan_office_list_url(2)),
        (("office", 1, "recheck"), asan_office_list_url(1)),
    ]
    remaining_items.extend(
        (("learning", page, "data"), asan_learning_list_url(page))
        for page in range(2, learning_last + 1)
    )
    remaining_items.extend(
        [
            (
                ("learning", learning_last + 1, "sentinel"),
                asan_learning_list_url(learning_last + 1),
            ),
            (("learning", 1, "recheck"), asan_learning_list_url(1)),
        ]
    )
    remaining_items.extend(
        (("media", page, "data"), asan_media_list_url(page))
        for page in range(2, media_last + 1)
    )
    remaining_items.extend(
        [
            (
                ("media", media_last + 1, "sentinel"),
                asan_media_list_url(media_last + 1),
            ),
            (("media", 1, "recheck"), asan_media_list_url(1)),
        ]
    )
    remaining, remaining_errors = _fetch_many(
        remaining_items,
        fetcher=current_fetcher,
        session_factory=current_factory,
        timeout=request_timeout,
        max_workers=workers,
    )
    fetched.update(remaining)
    errors.extend(remaining_errors)
    meta["pages"] += len(remaining)
    meta["list_requests"] += len(remaining)

    # Institution sentinel and page-one stability.
    office_sentinel = fetched.get(("office", 2, "sentinel"))
    office_recheck = fetched.get(("office", 1, "recheck"))
    if office_sentinel is None or office_recheck is None:
        errors.append("missing institution sentinel/recheck")
    else:
        try:
            sentinel_total, sentinel_current, sentinel_last = _total(office_sentinel)
            recheck_total, recheck_current, recheck_last = _total(office_recheck)
        except ValueError as exc:
            errors.append(f"office sentinel/recheck: {_clean(exc)}")
        else:
            sentinel_offices, sentinel_errors = _parse_offices(office_sentinel)
            recheck_offices, recheck_errors = _parse_offices(office_recheck)
            errors.extend(sentinel_errors)
            errors.extend(recheck_errors)
            if (
                (sentinel_total, sentinel_current, sentinel_last)
                != (office_total, 2, office_last)
                or sentinel_offices
            ):
                errors.append("institution immediate post-last page is not empty")
            if (
                (recheck_total, recheck_current, recheck_last)
                != (office_total, 1, office_last)
                or recheck_offices != offices
            ):
                errors.append("institution page-one recheck changed")

    learning_rows: list[dict[str, Any]] = []
    learning_page_counts: dict[int, int] = {}
    learning_signatures: dict[int, str] = {}
    for page in range(1, learning_last + 1):
        soup = learning_soup if page == 1 else fetched.get(("learning", page, "data"))
        if soup is None:
            errors.append(f"learning page {page}: missing response")
            continue
        try:
            total, current, last = _total(soup)
        except ValueError as exc:
            errors.append(f"learning page {page}: {_clean(exc)}")
            continue
        if (total, current, last) != (learning_total, page, learning_last):
            errors.append(f"learning page {page}: total/page marker changed")
        if page == 1:
            rows = first_learning_rows
        else:
            rows, row_errors = _parse_learning_page(soup, page=page, cutoff=cutoff)
            errors.extend(row_errors)
        expected_count = (
            ASAN_PAGE_SIZE
            if page < learning_last
            else learning_total - ASAN_PAGE_SIZE * (learning_last - 1)
        )
        if learning_total == 0:
            expected_count = 0
        if len(rows) != expected_count:
            errors.append(f"learning page {page}: row count mismatch")
        expected_sequences = list(
            range(
                learning_total - ASAN_PAGE_SIZE * (page - 1),
                learning_total - ASAN_PAGE_SIZE * (page - 1) - len(rows),
                -1,
            )
        )
        actual_sequences = [row["raw_fields"]["list_sequence"] for row in rows]
        if actual_sequences != expected_sequences:
            errors.append(f"learning page {page}: source sequence gap/reorder")
        learning_page_counts[page] = len(rows)
        learning_signatures[page] = _page_signature(rows)
        learning_rows.extend(rows)
    nonempty_learning_signatures = [
        learning_signatures[page]
        for page in range(1, learning_last + 1)
        if learning_page_counts.get(page)
    ]
    if len(nonempty_learning_signatures) != len(set(nonempty_learning_signatures)):
        errors.append("duplicate non-empty learning page signature")
    if len(learning_rows) != learning_total:
        errors.append("learning declared total does not match parsed rows")

    learning_sentinel = fetched.get(("learning", learning_last + 1, "sentinel"))
    learning_recheck = fetched.get(("learning", 1, "recheck"))
    if learning_sentinel is None or learning_recheck is None:
        errors.append("missing learning sentinel/recheck")
    else:
        sentinel_rows, sentinel_errors = _parse_learning_page(
            learning_sentinel, page=learning_last + 1, cutoff=cutoff
        )
        recheck_rows, recheck_errors = _parse_learning_page(
            learning_recheck, page=1, cutoff=cutoff
        )
        errors.extend(sentinel_errors)
        errors.extend(recheck_errors)
        try:
            sentinel_marker = _total(learning_sentinel)
            recheck_marker = _total(learning_recheck)
        except ValueError as exc:
            errors.append(f"learning sentinel/recheck: {_clean(exc)}")
        else:
            if sentinel_marker != (
                learning_total,
                learning_last + 1,
                learning_last,
            ) or sentinel_rows:
                errors.append("learning immediate post-last page is not empty")
            if recheck_marker != (learning_total, 1, learning_last) or (
                _page_signature(recheck_rows) != learning_signatures.get(1)
            ):
                errors.append("learning page-one recheck changed")

    media_rows: list[dict[str, Any]] = []
    media_signatures: dict[int, str] = {}
    for page in range(1, media_last + 1):
        soup = media_soup if page == 1 else fetched.get(("media", page, "data"))
        if soup is None:
            errors.append(f"media page {page}: missing response")
            continue
        try:
            total, current, last = _total(soup)
        except ValueError as exc:
            errors.append(f"media page {page}: {_clean(exc)}")
            continue
        if (total, current, last) != (media_total, page, media_last):
            errors.append(f"media page {page}: total/page marker changed")
        if page == 1:
            rows = first_media_rows
        else:
            rows, row_errors = _parse_media_page(soup, page=page)
            errors.extend(row_errors)
        expected_count = (
            ASAN_MEDIA_PAGE_SIZE
            if page < media_last
            else media_total - ASAN_MEDIA_PAGE_SIZE * (media_last - 1)
        )
        if media_total == 0:
            expected_count = 0
        if len(rows) != expected_count:
            errors.append(f"media page {page}: row count mismatch")
        media_signatures[page] = _page_signature(rows)
        media_rows.extend(rows)
    if len(media_rows) != media_total:
        errors.append("media declared total does not match parsed rows")
    media_sentinel = fetched.get(("media", media_last + 1, "sentinel"))
    media_recheck = fetched.get(("media", 1, "recheck"))
    if media_sentinel is None or media_recheck is None:
        errors.append("missing media sentinel/recheck")
    else:
        sentinel_rows, sentinel_errors = _parse_media_page(
            media_sentinel, page=media_last + 1
        )
        recheck_rows, recheck_errors = _parse_media_page(media_recheck, page=1)
        errors.extend(sentinel_errors)
        errors.extend(recheck_errors)
        try:
            sentinel_marker = _total(media_sentinel)
            recheck_marker = _total(media_recheck)
        except ValueError as exc:
            errors.append(f"media sentinel/recheck: {_clean(exc)}")
        else:
            if sentinel_marker != (media_total, media_last + 1, media_last) or sentinel_rows:
                errors.append("media immediate post-last page is not empty")
            if recheck_marker != (media_total, 1, media_last) or (
                _page_signature(recheck_rows) != media_signatures.get(1)
            ):
                errors.append("media page-one recheck changed")

    identities = [
        _clean(row.get("raw_fields", {}).get("identity"))
        for row in learning_rows + media_rows
    ]
    duplicate_identity_count = len(identities) - len(set(identities))
    if duplicate_identity_count:
        errors.append(f"{duplicate_identity_count} duplicate source identities")
    office_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_office_name"))
        for row in learning_rows
        if not row.get("raw_fields", {}).get("excluded_development_office")
    )
    unknown_offices = set(office_counts) - set(ASAN_OFFICE_BY_NAME)
    if unknown_offices:
        errors.append("learning archive contains an unknown visible institution")

    excluded_development_rows = [
        row
        for row in learning_rows
        if row.get("raw_fields", {}).get("excluded_development_office")
    ]
    excluded_facility_rows = [
        row
        for row in learning_rows
        if row.get("raw_fields", {}).get("excluded_facility_type")
    ]
    eligible_learning_rows = [
        row
        for row in learning_rows
        if not row.get("raw_fields", {}).get("excluded_development_office")
        and not row.get("raw_fields", {}).get("excluded_facility_type")
    ]
    current_learning_rows = [
        row
        for row in eligible_learning_rows
        if date.fromisoformat(_clean(row.get("end_date"))) >= cutoff
    ]
    expired_learning_count = len(eligible_learning_rows) - len(current_learning_rows)

    list_complete = bool(
        not errors
        and len(learning_rows) == learning_total
        and len(media_rows) == media_total
        and _offices_match_expected(offices, office_total)
    )
    required_details = len(current_learning_rows) + len(media_rows)
    if required_details > allowed_details:
        meta["source_cap_reached"] = True
        errors.append(
            f"detail_limit cap allows {allowed_details} of {required_details} required details"
        )

    detail_attempts = 0
    detail_pages = 0
    detail_errors: list[str] = []
    media_current_rows: list[dict[str, Any]] = []
    if list_complete and not errors:
        detail_items: list[tuple[Any, str]] = []
        for row in current_learning_rows + media_rows:
            catalogue = _clean(row.get("raw_fields", {}).get("catalogue"))
            identity = _clean(row.get("raw_fields", {}).get("identity"))
            detail_items.append(((catalogue, identity), _clean(row.get("raw_url"))))
        detail_attempts = len(detail_items)
        details, fetch_detail_errors = _fetch_many(
            detail_items,
            fetcher=current_fetcher,
            session_factory=current_factory,
            timeout=request_timeout,
            max_workers=workers,
        )
        detail_errors.extend(fetch_detail_errors)
        meta["pages"] += len(details)
        rows_by_key = {
            (
                _clean(row.get("raw_fields", {}).get("catalogue")),
                _clean(row.get("raw_fields", {}).get("identity")),
            ): row
            for row in current_learning_rows + media_rows
        }
        for key, soup in details.items():
            row = rows_by_key[key]
            if key[0] == "learning":
                item_errors = _validate_learning_detail(row, soup)
                current = True
            else:
                current, item_errors = _validate_media_detail(
                    row, soup, cutoff=cutoff
                )
                if current:
                    media_current_rows.append(row)
            if item_errors:
                detail_errors.extend(item_errors)
            else:
                detail_pages += 1
    errors.extend(detail_errors)

    details_complete = bool(
        list_complete
        and detail_attempts == required_details
        and detail_pages == required_details
        and not detail_errors
    )
    result: list[dict[str, Any]] = []
    current_rows = current_learning_rows + media_current_rows
    if list_complete and details_complete and not errors:
        for row in current_rows:
            errors.extend(_privacy_errors(row))
        if not errors:
            deduper = dedupe_rows or _dedupe_default
            result = list(deduper(current_rows))
            if len(result) != len(current_rows):
                errors.append(
                    f"dedupe changed complete row count {len(current_rows)} to {len(result)}"
                )
                result = []
    snapshot_complete = bool(list_complete and details_complete and not errors)
    if not snapshot_complete:
        result = []

    type_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_learning_type"))
        for row in learning_rows
    )
    status_counts = Counter(
        _clean(row.get("raw_fields", {}).get("source_status"))
        for row in learning_rows
    )
    branch_counts = Counter(_clean(row.get("branch")) for row in result)
    category_counts = Counter(_clean(row.get("category")) for row in result)
    meta.update(
        {
            "source_total": learning_total,
            "source_rows": len(learning_rows),
            "media_source_total": media_total,
            "media_source_rows": len(media_rows),
            "combined_source_rows": len(learning_rows) + len(media_rows),
            "declared_learning_pages": learning_last,
            "declared_media_pages": media_last,
            "learning_page_counts": learning_page_counts,
            "office_source_counts": dict(office_counts),
            "visible_office_source_rows": sum(office_counts.values()),
            "excluded_development_count": len(excluded_development_rows),
            "excluded_facility_count": len(excluded_facility_rows),
            "excluded_development_facility_overlap_count": sum(
                bool(row.get("raw_fields", {}).get("excluded_facility_type"))
                for row in excluded_development_rows
            ),
            "eligible_learning_source_count": len(eligible_learning_rows),
            "expired_learning_count": expired_learning_count,
            "current_learning_count": len(current_learning_rows),
            "current_media_count": len(media_current_rows),
            "current_count": len(current_rows),
            "returned_count": len(result),
            "detail_attempts": detail_attempts,
            "detail_pages": detail_pages,
            "detail_errors": len(detail_errors),
            "reservation_discovery_links": sum(
                bool(row.get("application_url")) for row in result
            ),
            "source_type_counts": dict(type_counts),
            "source_status_counts": dict(status_counts),
            "branch_count": len(branch_counts),
            "branch_counts": dict(branch_counts),
            "category_counts": dict(category_counts),
            "duplicate_identity_count": duplicate_identity_count,
            "pagination_detected": learning_last > 1 or media_last > 1,
            "pagination_complete": list_complete,
            "details_complete": details_complete,
            "snapshot_complete": snapshot_complete,
            "no_current_data": bool(snapshot_complete and not current_rows),
            "no_current_reason": (
                "all complete Asan education catalogues have ended"
                if snapshot_complete and not current_rows
                else ""
            ),
            "configured_collection_error": "; ".join(dict.fromkeys(errors)),
        }
    )
    return result, ASAN_PARSER, meta


collect = collect_asan_courses


__all__ = [
    "ASAN_ALIAS_PROVIDERS",
    "ASAN_ALLOWED_LEARNING_TYPES",
    "ASAN_CANONICAL_CANDIDATE_ID",
    "ASAN_CANONICAL_URL",
    "ASAN_EXCLUDED_LEARNING_TYPES",
    "ASAN_EXCLUDED_NON_COURSE_URLS",
    "ASAN_EXPECTED_OFFICES",
    "ASAN_HIDDEN_DEVELOPMENT_OFFICE",
    "ASAN_MEDIA_URL",
    "ASAN_MUNICIPALITY_CODE",
    "ASAN_MUNICIPALITY_NAME",
    "ASAN_OFFICE_URL",
    "ASAN_OWNERSHIP_ALIAS_URLS",
    "ASAN_PAGE_SIZE",
    "ASAN_PARSER",
    "ASAN_PROVIDER",
    "AsanOffice",
    "asan_learning_detail_url",
    "asan_learning_list_url",
    "asan_media_detail_url",
    "asan_media_list_url",
    "asan_office_list_url",
    "collect",
    "collect_asan_courses",
    "is_asan_education_target",
    "is_asan_excluded_non_course_target",
    "is_asan_ownership_alias_target",
    "is_target",
]
