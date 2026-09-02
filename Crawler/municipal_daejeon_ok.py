"""Fail-closed collectors for Daejeon's OK reservation catalogues.

The metropolitan OK service is one official reservation catalogue, but it is
not an aggregate of the five district lifelong-learning sites.  This module
therefore owns only the explicit education and experience leaves exposed by OK
itself.  The official district selector is used to make each catalogue
complete and to retain row-location evidence; it is never treated as evidence
that the independent district catalogues are aliases.

A snapshot is emitted only after each global declaration reconciles exactly
with all category/district partitions, every declared page and immediate
empty sentinel has been read, first pages remain stable, and every current or
future row passes its static-detail, AJAX-detail, application-control, and
district-attribution contracts.  Partial snapshots fail closed.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
import html
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


DAEJEON_OK_PROVIDER = "DAEJEON_OK_RESERVATION"
DAEJEON_OK_HOST = "www.daejeon.go.kr"
DAEJEON_OK_CITY_CODE = "3000000000"
DAEJEON_OK_CITY_NAME = "대전광역시"
DAEJEON_OK_CANONICAL_URL = (
    "https://www.daejeon.go.kr/okr2019/eduRsvtList.do?"
    "menuSeq=8100&ntatcDelYn=Y&boardUseYn=N&menuUseYn=N"
)
DAEJEON_OK_LIST_ENDPOINT = (
    "https://www.daejeon.go.kr/okr2019/eduRsvtList.do"
)
DAEJEON_OK_DETAIL_ENDPOINT = (
    "https://www.daejeon.go.kr/okr2019/eduRsvtDtl.do"
)
DAEJEON_OK_DETAIL_AJAX_ENDPOINT = (
    "https://www.daejeon.go.kr/okr2019/ajaxSelectEduRsvtDtlInfo.do"
)
DAEJEON_OK_EXPERIENCE_CANONICAL_URL = (
    "https://www.daejeon.go.kr/okr2019/expRsvtList.do?"
    "menuSeq=8201&ntatcDelYn=Y&boardUseYn=N&menuUseYn=N"
)
DAEJEON_OK_EXPERIENCE_LIST_ENDPOINT = (
    "https://www.daejeon.go.kr/okr2019/expRsvtList.do"
)
DAEJEON_OK_EXPERIENCE_DETAIL_ENDPOINT = (
    "https://www.daejeon.go.kr/okr2019/expRsvtDtl.do"
)
DAEJEON_OK_EXPERIENCE_DETAIL_AJAX_ENDPOINT = (
    "https://www.daejeon.go.kr/okr2019/ajaxSelectExpRsvtDtlInfo.do"
)
DAEJEON_OK_PAGE_SIZE = 10
DAEJEON_OK_FETCH_ATTEMPTS = 3
DAEJEON_OK_MAX_WORKERS = 4
DAEJEON_OK_RETRY_DELAYS = (1.0, 3.0)
DAEJEON_OK_PARSER = (
    "daejeon_ok_education_two_leaves+five_regions+global_reconcile+"
    "empty_sentinels+stable_recheck+current_ajax_detail+persistent_worker_pool"
)
DAEJEON_OK_OWNERSHIP_SCOPE = (
    "daejeon_ok_reservation_8101_8102_all_official_service_regions"
)
DAEJEON_OK_EXPERIENCE_PARSER = (
    "daejeon_ok_experience_one_leaf+five_regions+global_reconcile+"
    "empty_sentinels+stable_recheck+current_ajax_detail+persistent_worker_pool"
)
DAEJEON_OK_EXPERIENCE_OWNERSHIP_SCOPE = (
    "daejeon_ok_reservation_8201_all_official_service_regions"
)


@dataclass(frozen=True)
class DaejeonOkCategory:
    menu_seq: str
    label: str


@dataclass(frozen=True)
class DaejeonOkDistrict:
    source_code: str
    label: str
    municipality_code: str
    municipality_full_name: str


@dataclass(frozen=True)
class DaejeonOkSource:
    scope: str
    canonical_url: str
    list_endpoint: str
    detail_endpoint: str
    detail_ajax_endpoint: str
    list_type_field: str
    categories: tuple[DaejeonOkCategory, ...]
    parser: str
    ownership_scope: str
    program_type: str
    domain_category: str
    service_group: str
    excluded_menus: Mapping[str, tuple[str, ...]]
    include_independent_noncoverage: bool = False


DAEJEON_OK_CATEGORIES: tuple[DaejeonOkCategory, ...] = (
    DaejeonOkCategory("8101", "강좌"),
    DaejeonOkCategory("8102", "교육"),
)
DAEJEON_OK_EXPERIENCE_CATEGORIES: tuple[DaejeonOkCategory, ...] = (
    DaejeonOkCategory("8201", "체험"),
)
DAEJEON_OK_DISTRICTS: tuple[DaejeonOkDistrict, ...] = (
    DaejeonOkDistrict("001", "동구", "3011000000", "대전광역시 동구"),
    DaejeonOkDistrict("002", "중구", "3014000000", "대전광역시 중구"),
    DaejeonOkDistrict("003", "서구", "3017000000", "대전광역시 서구"),
    DaejeonOkDistrict("004", "유성구", "3020000000", "대전광역시 유성구"),
    DaejeonOkDistrict("005", "대덕구", "3023000000", "대전광역시 대덕구"),
)

# These live title-search misses prevent the official district catalogues from
# being collapsed into the metropolitan provider.  They are audit metadata,
# not executable aliases or asserted exhaustive differences.
DAEJEON_OK_INDEPENDENT_CATALOGUE_NONCOVERAGE: tuple[Mapping[str, Any], ...] = (
    {
        "municipality_code": "3014000000",
        "municipality_full_name": "대전광역시 중구",
        "independent_url": (
            "https://www.djjunggu.go.kr/prog/lecCourse/lec/lll/"
            "sub02_01_02/list.do"
        ),
        "sample_title": "2026년 환경교육 실천가 과정",
        "ok_title_search_totals": {"8101": 0, "8102": 0},
        "checked_on": "2026-07-21",
    },
    {
        "municipality_code": "3023000000",
        "municipality_full_name": "대전광역시 대덕구",
        "independent_url": (
            "https://lll.daedeok.go.kr/lms/damoa/contents/dms/edu/05/"
            "edu.05.001.motion?mnucd=MENU0100021"
        ),
        "sample_title": "대덕미래아카데미",
        "ok_title_search_totals": {"8101": 0, "8102": 0},
        "checked_on": "2026-07-21",
    },
)

DAEJEON_OK_EXCLUDED_MENUS: Mapping[str, tuple[str, ...]] = {
    "experience_observation": ("8200", "8201", "8203"),
    "farm": ("8105",),
    "facility_rental": ("8700", "8701", "8407", "8702"),
}
DAEJEON_OK_EXPERIENCE_EXCLUDED_MENUS: Mapping[str, tuple[str, ...]] = {
    "education": ("8100", "8101", "8102"),
    "experience_observation_siblings": ("8200", "8203"),
    "farm": ("8105",),
    "facility_rental": ("8700", "8701", "8407", "8702"),
}

DAEJEON_OK_EDUCATION_SOURCE = DaejeonOkSource(
    scope="education",
    canonical_url=DAEJEON_OK_CANONICAL_URL,
    list_endpoint=DAEJEON_OK_LIST_ENDPOINT,
    detail_endpoint=DAEJEON_OK_DETAIL_ENDPOINT,
    detail_ajax_endpoint=DAEJEON_OK_DETAIL_AJAX_ENDPOINT,
    list_type_field="eduRsvtListType",
    categories=DAEJEON_OK_CATEGORIES,
    parser=DAEJEON_OK_PARSER,
    ownership_scope=DAEJEON_OK_OWNERSHIP_SCOPE,
    program_type="교육",
    domain_category="교육·강좌",
    service_group="공공강좌",
    excluded_menus=DAEJEON_OK_EXCLUDED_MENUS,
    include_independent_noncoverage=True,
)
DAEJEON_OK_EXPERIENCE_SOURCE = DaejeonOkSource(
    scope="experience",
    canonical_url=DAEJEON_OK_EXPERIENCE_CANONICAL_URL,
    list_endpoint=DAEJEON_OK_EXPERIENCE_LIST_ENDPOINT,
    detail_endpoint=DAEJEON_OK_EXPERIENCE_DETAIL_ENDPOINT,
    detail_ajax_endpoint=DAEJEON_OK_EXPERIENCE_DETAIL_AJAX_ENDPOINT,
    list_type_field="expRsvtListType",
    categories=DAEJEON_OK_EXPERIENCE_CATEGORIES,
    parser=DAEJEON_OK_EXPERIENCE_PARSER,
    ownership_scope=DAEJEON_OK_EXPERIENCE_OWNERSHIP_SCOPE,
    program_type="체험",
    domain_category="체험·견학",
    service_group="체험",
    excluded_menus=DAEJEON_OK_EXPERIENCE_EXCLUDED_MENUS,
)

SessionFactory = Callable[[], Any]
HtmlFetcher = Callable[[Any, str, int], Any]
JsonPostFetcher = Callable[[Any, str, Mapping[str, Any], int], Any]
DedupeRows = Callable[[list[dict[str, Any]]], Iterable[dict[str, Any]]]

_SPACE_RE = re.compile(r"\s+")
_COUNTER_RE = re.compile(
    r"^총\s*([\d,]+)\s*건\s*\|\s*(\d+)\s*/\s*(\d+)\s*페이지$"
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?!\d)")
_TIME_RANGE_RE = re.compile(r"(\d{1,2}:\d{2})\s*~\s*(\d{1,2}:\d{2})")
_CAPACITY_RE = re.compile(r"^([\d,]+)\s*/\s*([\d,]+)$")
_DETAIL_LINK_RE = re.compile(
    r"^javascript:\s*moveToFcltInfoMngDetail\("
    r"'([A-Z0-9]{1,20})',\s*(\d+),\s*(\d+),\s*(\d+),\s*"
    r"'(\d{3})',\s*'(\d{3})',\s*'(\d*)',\s*'tab2'\s*\);?$"
)
_SCRIPT_VALUE_TEMPLATE = r"var\s+{name}\s*=\s*'([^']*)'\s*;"
_SAFE_ID_RE = re.compile(r"[A-Z0-9]{1,20}")
_DIGITS_RE = re.compile(r"\d+")

_EXPECTED_HEADERS = (
    "번호",
    "시설명",
    "예약명",
    "모집방법",
    "접수기간",
    "이용기간",
    "수강료",
    "접수자/정원",
    "접수상태",
)
_EXPECTED_EXPERIENCE_HEADERS = (
    "번호",
    "시설명",
    "예약명",
    "모집방법",
    "접수기간",
    "이용기간",
    "수강료",
    "접수상태",
)
_EXPECTED_DISTRICT_OPTIONS = (
    ("000", "전체"),
    ("001", "동구"),
    ("002", "중구"),
    ("003", "서구"),
    ("004", "유성구"),
    ("005", "대덕구"),
)
_EXPECTED_STATUS_OPTIONS = (
    ("", "전체"),
    ("001", "접수대기"),
    ("002", "접수중"),
    ("003", "인원마감"),
    ("004", "접수종료"),
    ("005", "대기자 접수중"),
)
_EXPECTED_EXPERIENCE_STATUS_OPTIONS = _EXPECTED_STATUS_OPTIONS[:-1]
_STATUS_LABELS: Mapping[str, str] = {
    "001": "접수대기",
    "002": "접수중",
    "003": "인원마감",
    "004": "접수종료",
    "005": "대기자접수",
}
_METHOD_LABELS: Mapping[str, str] = {
    "001": "선착순",
    "002": "추첨식",
    "004": "서류접수예약",
}
_NORMALIZED_STATUS: Mapping[str, str] = {
    "001": "SCHEDULED",
    "002": "OPEN",
    "003": "CLOSED",
    "004": "CLOSED",
    "005": "WAITLIST",
}
_ACTIVE_STATUS_CODES = frozenset({"002", "005"})


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(
        " ", html.unescape(str(value or "")).replace("\xa0", " ")
    ).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _clean(value).casefold(), flags=re.UNICODE)


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


def _matches_source_target(target: Any, source: DaejeonOkSource) -> bool:
    if _clean(_target_value(target, "provider")) != DAEJEON_OK_PROVIDER:
        return False
    parsed = urlparse(_clean(_target_value(target, "url")))
    canonical = urlparse(source.canonical_url)
    if not (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").rstrip(".").lower() == DAEJEON_OK_HOST
        and parsed.port is None
        and parsed.path == canonical.path
        and not parsed.params
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    ):
        return False
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    expected_pairs = parse_qsl(canonical.query, keep_blank_values=True)
    return len(pairs) == len(expected_pairs) and dict(pairs) == dict(expected_pairs)


def is_daejeon_ok_education_target(target: Any) -> bool:
    """Match only the metropolitan OK education owner."""

    return _matches_source_target(target, DAEJEON_OK_EDUCATION_SOURCE)


def is_daejeon_ok_experience_target(target: Any) -> bool:
    """Match only the metropolitan OK experience owner."""

    return _matches_source_target(target, DAEJEON_OK_EXPERIENCE_SOURCE)


def _source_for_target(target: Any) -> Optional[DaejeonOkSource]:
    for source in (DAEJEON_OK_EDUCATION_SOURCE, DAEJEON_OK_EXPERIENCE_SOURCE):
        if _matches_source_target(target, source):
            return source
    return None


def is_daejeon_ok_target(target: Any) -> bool:
    return _source_for_target(target) is not None


is_target = is_daejeon_ok_target


def _list_url(
    source: DaejeonOkSource, menu_seq: Any, district_code: Any, page: int
) -> str:
    menu = _clean(menu_seq)
    district = _clean(district_code)
    if menu not in {item.menu_seq for item in source.categories}:
        return ""
    if district not in {"000", *(item.source_code for item in DAEJEON_OK_DISTRICTS)}:
        return ""
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        return ""
    return source.list_endpoint + "?" + urlencode(
        {
            "menuSeq": menu,
            "ntatcDelYn": "Y",
            "boardUseYn": "N",
            "menuUseYn": "N",
            "cityProvinceTpcd": district,
            "pageIdx": page,
        }
    )


def daejeon_ok_list_url(menu_seq: Any, district_code: Any, page: int) -> str:
    return _list_url(DAEJEON_OK_EDUCATION_SOURCE, menu_seq, district_code, page)


def daejeon_ok_experience_list_url(
    menu_seq: Any, district_code: Any, page: int
) -> str:
    return _list_url(DAEJEON_OK_EXPERIENCE_SOURCE, menu_seq, district_code, page)


def _detail_url(
    source: DaejeonOkSource,
    menu_seq: Any,
    itecd: Any,
    facility_seq: Any,
    reservation_seq: Any,
    reservation_detail_seq: Any,
    status_code: Any,
    dgr: Any,
) -> str:
    menu = _clean(menu_seq)
    agency = _clean(itecd)
    facility = _clean(facility_seq)
    reservation = _clean(reservation_seq)
    detail = _clean(reservation_detail_seq)
    status = _clean(status_code)
    generation = _clean(dgr)
    if (
        menu not in {item.menu_seq for item in source.categories}
        or not _SAFE_ID_RE.fullmatch(agency)
        or not all(_DIGITS_RE.fullmatch(value) for value in (facility, reservation, detail))
        or status not in _STATUS_LABELS
        or (generation and not _DIGITS_RE.fullmatch(generation))
    ):
        return ""
    return source.detail_endpoint + "?" + urlencode(
        {
            "itecd": agency,
            "fcltSeq": facility,
            "rsvtSeq": reservation,
            "rsvtUseDtlSeq": detail,
            "tabNo": "tab2",
            "menuSeq": menu,
            "statCd": status,
            "dgr": generation,
        }
    )


def daejeon_ok_detail_url(
    menu_seq: Any,
    itecd: Any,
    facility_seq: Any,
    reservation_seq: Any,
    reservation_detail_seq: Any,
    status_code: Any,
    dgr: Any,
) -> str:
    return _detail_url(
        DAEJEON_OK_EDUCATION_SOURCE,
        menu_seq,
        itecd,
        facility_seq,
        reservation_seq,
        reservation_detail_seq,
        status_code,
        dgr,
    )


def daejeon_ok_experience_detail_url(
    menu_seq: Any,
    itecd: Any,
    facility_seq: Any,
    reservation_seq: Any,
    reservation_detail_seq: Any,
    status_code: Any,
    dgr: Any,
) -> str:
    return _detail_url(
        DAEJEON_OK_EXPERIENCE_SOURCE,
        menu_seq,
        itecd,
        facility_seq,
        reservation_seq,
        reservation_detail_seq,
        status_code,
        dgr,
    )


def _default_session_factory() -> requests.Session:
    current = requests.Session()
    current.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    return current


class _SessionLease:
    """Expose one pooled session while keeping per-request close calls harmless."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self.session, name)

    def close(self) -> None:
        return None


class _ThreadSessionPool:
    """Reuse one requests session per worker thread for the duration of a crawl."""

    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions: list[Any] = []
        self._closed = False

    def __call__(self) -> _SessionLease:
        lease = getattr(self._local, "lease", None)
        if lease is not None:
            return lease
        session = self._factory()
        lease = _SessionLease(session)
        with self._lock:
            if self._closed:
                _close_quietly(session)
                raise RuntimeError("Daejeon OK session pool is closed")
            self._sessions.append(session)
        self._local.lease = lease
        return lease

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            _close_quietly(session)


def _close_quietly(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _sleep_before_retry(attempt: int, attempts: int) -> None:
    if attempt >= attempts:
        return
    index = min(attempt - 1, len(DAEJEON_OK_RETRY_DELAYS) - 1)
    time.sleep(DAEJEON_OK_RETRY_DELAYS[index])


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code", 0))
    except (TypeError, ValueError):
        return 0


def _strict_response(response: Any, label: str, content_type: str) -> None:
    status = _response_status(response)
    if status != 200:
        raise ValueError(f"{label}: unexpected HTTP status {status}")
    if getattr(response, "history", None):
        raise ValueError(f"{label}: redirects are not accepted")
    headers = getattr(response, "headers", {})
    actual = _clean(headers.get("Content-Type") if isinstance(headers, Mapping) else "")
    if content_type not in actual.lower():
        raise ValueError(f"{label}: unexpected content type {actual!r}")


def _default_html_fetcher(session: Any, url: str, timeout: int) -> BeautifulSoup:
    response = session.get(
        url,
        timeout=timeout,
        verify=True,
        allow_redirects=False,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    _strict_response(response, "HTML request", "text/html")
    return BeautifulSoup(response.content, "lxml")


def _default_json_post_fetcher(
    session: Any, url: str, payload: Mapping[str, Any], timeout: int
) -> Mapping[str, Any]:
    referer = (
        DAEJEON_OK_EXPERIENCE_DETAIL_ENDPOINT
        if url == DAEJEON_OK_EXPERIENCE_DETAIL_AJAX_ENDPOINT
        else DAEJEON_OK_DETAIL_ENDPOINT
    )
    response = session.post(
        url,
        data=dict(payload),
        timeout=timeout,
        verify=True,
        allow_redirects=False,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    _strict_response(response, "detail AJAX request", "application/json")
    try:
        value = response.json()
    except Exception as exc:
        raise ValueError("detail AJAX request: invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("detail AJAX request: root is not an object")
    return value


def _as_soup(value: Any, label: str) -> BeautifulSoup:
    if isinstance(value, BeautifulSoup):
        return value
    if isinstance(value, str):
        return BeautifulSoup(value, "lxml")
    content = getattr(value, "content", None)
    if isinstance(content, bytes):
        return BeautifulSoup(content, "lxml")
    raise ValueError(f"{label}: fetcher did not return HTML")


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    json_method = getattr(value, "json", None)
    if callable(json_method):
        decoded = json_method()
        if isinstance(decoded, Mapping):
            return decoded
    raise ValueError(f"{label}: fetcher did not return a JSON object")


def _fetch_html(
    url: str,
    *,
    session_factory: SessionFactory,
    fetcher: HtmlFetcher,
    timeout: int,
    attempts: int,
    label: str,
) -> BeautifulSoup:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        session = session_factory()
        try:
            return _as_soup(fetcher(session, url, timeout), label)
        except Exception as exc:
            errors.append(f"attempt {attempt}: {_clean(exc)}")
        finally:
            _close_quietly(session)
        _sleep_before_retry(attempt, attempts)
    raise ValueError(f"{label}: " + " | ".join(errors))


def _post_json(
    payload: Mapping[str, Any],
    *,
    endpoint: str = DAEJEON_OK_DETAIL_AJAX_ENDPOINT,
    session_factory: SessionFactory,
    fetcher: JsonPostFetcher,
    timeout: int,
    attempts: int,
    label: str,
) -> Mapping[str, Any]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        session = session_factory()
        try:
            return _as_mapping(
                fetcher(session, endpoint, payload, timeout),
                label,
            )
        except Exception as exc:
            errors.append(f"attempt {attempt}: {_clean(exc)}")
        finally:
            _close_quietly(session)
        _sleep_before_retry(attempt, attempts)
    raise ValueError(f"{label}: " + " | ".join(errors))


def _fetch_detail_pair(
    url: str,
    payload: Mapping[str, Any],
    *,
    ajax_endpoint: str = DAEJEON_OK_DETAIL_AJAX_ENDPOINT,
    session_factory: SessionFactory,
    html_fetcher: HtmlFetcher,
    json_fetcher: JsonPostFetcher,
    timeout: int,
    attempts: int,
    label: str,
) -> tuple[BeautifulSoup, Mapping[str, Any]]:
    """Fetch static and AJAX detail through one short-lived keep-alive session."""

    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        session = session_factory()
        try:
            soup = _as_soup(html_fetcher(session, url, timeout), f"{label} HTML")
            ajax = _as_mapping(
                json_fetcher(
                    session,
                    ajax_endpoint,
                    payload,
                    timeout,
                ),
                f"{label} AJAX",
            )
            return soup, ajax
        except Exception as exc:
            errors.append(f"attempt {attempt}: {_clean(exc)}")
        finally:
            _close_quietly(session)
        _sleep_before_retry(attempt, attempts)
    raise ValueError(f"{label}: " + " | ".join(errors))


def _options(select: Any) -> tuple[tuple[str, str], ...]:
    if select is None:
        return ()
    return tuple(
        (_clean(option.get("value")), _clean(option.get_text(" ", strip=True)))
        for option in select.select("option")
    )


def _date_pair(
    value: Any, label: str, *, allow_reversed: bool = False
) -> tuple[date, date]:
    matches = _DATE_RE.findall(_clean(value))
    if len(matches) not in {1, 2}:
        raise ValueError(f"{label}: expected one or two dates")
    parsed: list[date] = []
    for year, month, day in matches:
        try:
            parsed.append(date(int(year), int(month), int(day)))
        except ValueError as exc:
            raise ValueError(f"{label}: invalid date") from exc
    start = parsed[0]
    end = parsed[-1]
    if end < start and not allow_reversed:
        raise ValueError(f"{label}: reversed date range")
    return start, end


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label}: not an integer")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{label}: negative integer")
        return value
    raw = _clean(value).replace(",", "")
    if not raw or not raw.isdigit():
        raise ValueError(f"{label}: not an integer")
    return int(raw)


def _milliseconds_date(value: Any, label: str) -> date:
    if isinstance(value, bool):
        raise ValueError(f"{label}: invalid millisecond timestamp")
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid millisecond timestamp") from exc
    if milliseconds < 946684800000 or milliseconds > 4102444799999:
        raise ValueError(f"{label}: implausible millisecond timestamp")
    return datetime.fromtimestamp(
        milliseconds / 1000, tz=ZoneInfo("Asia/Seoul")
    ).date()


def _date_distance(left: date, right: date) -> int:
    return abs((left - right).days)


def _application_rule_evidence(
    reservation: Mapping[str, Any],
    facility_reservation: Mapping[str, Any],
    listed_apply: tuple[date, date],
    identity: str,
) -> tuple[str, str]:
    date_rule = _object(
        facility_reservation.get("rsvtDateVO"), f"{identity}.rsvtDateVO"
    )
    if _clean(date_rule.get("useYn")) != "Y":
        raise ValueError(f"{identity}: reservation-date rule is not enabled")
    rule_code = _clean(date_rule.get("rsvtPsblTpcd"))
    if rule_code not in {"000", "001", "002", "003", "004", "005", "006", "007", "999"}:
        raise ValueError(f"{identity}: unknown reservation-date rule {rule_code!r}")
    begin_time = _clean(date_rule.get("rsvtRcitBgtm"))
    end_time = _clean(date_rule.get("rsvtRcitEdtm"))
    if not re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d", begin_time):
        raise ValueError(f"{identity}: invalid reservation begin time")
    if not (
        re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d", end_time)
        or end_time == "2400"
    ):
        raise ValueError(f"{identity}: invalid reservation end time")

    evidence = f"relative_rule:{rule_code}"
    if rule_code == "999":
        begin = _milliseconds_date(
            date_rule.get("rsvtPsblBgdt"), f"{identity}.rsvtPsblBgdt"
        )
        end = _milliseconds_date(
            date_rule.get("rsvtPsblEddt"), f"{identity}.rsvtPsblEddt"
        )
        if end < begin:
            raise ValueError(f"{identity}: fixed reservation-date rule is reversed")
        # A small number of live rows differ by one inclusive end-day between
        # the server-rendered list and the AJAX rule.  The official UI uses
        # the list value for catalogue display, so tolerate only that observed
        # boundary convention and keep the list period authoritative.
        if (
            _date_distance(begin, listed_apply[0]) > 1
            or _date_distance(end, listed_apply[1]) > 1
        ):
            raise ValueError(f"{identity}: fixed reservation-date rule/list mismatch")
        evidence = f"fixed:{begin.isoformat()}~{end.isoformat()}"
    elif rule_code == "000":
        begin = _milliseconds_date(
            facility_reservation.get("operBgdt"), f"{identity}.operBgdt"
        )
        end = _milliseconds_date(
            facility_reservation.get("operEddt"), f"{identity}.operEddt"
        )
        if end < begin:
            raise ValueError(f"{identity}: operation reservation range is reversed")
        if (
            _date_distance(begin, listed_apply[0]) > 1
            or _date_distance(end, listed_apply[1]) > 1
        ):
            raise ValueError(f"{identity}: operation reservation rule/list mismatch")
        evidence = f"operation:{begin.isoformat()}~{end.isoformat()}"

    priority = _clean(reservation.get("priorityRsvtYn"))
    if priority not in {"", "Y", "N"}:
        raise ValueError(f"{identity}: invalid priority reservation flag")
    if priority == "Y":
        priority_start = _milliseconds_date(
            reservation.get("priorityRsvtBgdt"), f"{identity}.priorityRsvtBgdt"
        )
        priority_end = _milliseconds_date(
            reservation.get("priorityRsvtEddt"), f"{identity}.priorityRsvtEddt"
        )
        if priority_end < priority_start:
            raise ValueError(f"{identity}: priority reservation range is reversed")
    return rule_code, evidence


def _fee(value: Any) -> tuple[str, int]:
    raw = _clean(value)
    if raw == "무료":
        return "무료", 0
    if raw == "현장결제":
        return raw, 0
    if not re.fullmatch(r"[\d,]+\s*원?", raw):
        raise ValueError(f"invalid fee {raw!r}")
    digits = "".join(_DIGITS_RE.findall(raw))
    if not digits:
        raise ValueError(f"invalid fee {raw!r}")
    amount = int(digits)
    return f"{amount:,}원", amount


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    raw = row.get("raw_fields")
    if not isinstance(raw, Mapping):
        raise ValueError("row has no raw identity")
    return (
        _clean(raw.get("itecd")),
        _clean(raw.get("facility_seq")),
        _clean(raw.get("reservation_seq")),
        _clean(raw.get("reservation_detail_seq")),
        _clean(raw.get("dgr")),
    )


def _schema_errors(
    soup: BeautifulSoup,
    category: DaejeonOkCategory,
    page: int,
    source: DaejeonOkSource = DAEJEON_OK_EDUCATION_SOURCE,
) -> list[str]:
    errors: list[str] = []
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "OK예약서비스" not in title or "대전광역시" not in title:
        errors.append("official page title changed")
    forms = soup.select("form#pubFcltInfoListForm")
    if len(forms) != 1:
        return [*errors, "official list form is missing or duplicated"]
    form = forms[0]
    if _clean(form.get("method")).lower() != "post":
        errors.append("list form method changed")
    if _clean(form.get("action")) != urlparse(source.list_endpoint).path:
        errors.append("list form action changed")
    expected_hidden = {
        "pageIdx": str(page),
        "menuSeq": category.menu_seq,
        "fcltClsfcCd": "",
        source.list_type_field: "text",
    }
    for name, expected in expected_hidden.items():
        nodes = form.select(f'input[type="hidden"][name="{name}"]')
        if len(nodes) != 1 or _clean(nodes[0].get("value")) != expected:
            errors.append(f"hidden field {name} changed")
    if _options(form.select_one('select[name="cityProvinceTpcd"]')) != (
        _EXPECTED_DISTRICT_OPTIONS
    ):
        errors.append("official service-region selector changed")
    expected_status_options = (
        _EXPECTED_EXPERIENCE_STATUS_OPTIONS
        if source.scope == "experience"
        else _EXPECTED_STATUS_OPTIONS
    )
    if _options(form.select_one('select[name="rsvtUseStatTpcd"]')) != expected_status_options:
        errors.append("official status selector changed")
    # The production template closes the search form before rendering the
    # results table (the form remains the pagination/filter contract).
    tables = soup.select("table.ntable_styl")
    if len(tables) != 1:
        errors.append("official reservation table is missing or duplicated")
    else:
        headers = tuple(
            _clean(node.get_text(" ", strip=True))
            for node in tables[0].select("thead th")
        )
        expected_headers = (
            _EXPECTED_EXPERIENCE_HEADERS
            if source.scope == "experience"
            else _EXPECTED_HEADERS
        )
        if headers != expected_headers:
            errors.append("official reservation table headers changed")
    return errors


def _parse_list_page(
    soup: BeautifulSoup,
    category: DaejeonOkCategory,
    district: Optional[DaejeonOkDistrict],
    page: int,
    cutoff: date,
    source: DaejeonOkSource = DAEJEON_OK_EDUCATION_SOURCE,
) -> tuple[list[dict[str, Any]], int, int]:
    label = f"{category.menu_seq}/{district.source_code if district else '000'} page {page}"
    errors = _schema_errors(soup, category, page, source)
    counters = soup.select(".total_counter")
    counter_text = _clean(counters[0].get_text(" ", strip=True)) if len(counters) == 1 else ""
    match = _COUNTER_RE.fullmatch(counter_text)
    if not match:
        errors.append("declared total/page counter changed")
        total = 0
        response_page = 0
        last = 0
    else:
        total = int(match.group(1).replace(",", ""))
        response_page = int(match.group(2))
        last = int(match.group(3))
        expected_last = max(1, math.ceil(total / DAEJEON_OK_PAGE_SIZE))
        if response_page != page:
            errors.append("response page does not match request")
        if last != expected_last:
            errors.append("declared last page does not match total")

    tables = soup.select("table.ntable_styl")
    body_rows = tables[0].select("tbody tr") if len(tables) == 1 else []
    parsed: list[dict[str, Any]] = []
    empty_markers = 0
    capacity_present = source.scope == "education"
    expected_cell_count = 9 if capacity_present else 8
    status_index = 8 if capacity_present else 7
    for tr in body_rows:
        cells = tr.select("td")
        links = tr.select('a[href*="moveToFcltInfoMngDetail"]')
        text = _clean(tr.get_text(" ", strip=True))
        if not links:
            if (
                len(cells) == 1
                and _clean(cells[0].get("colspan")) == str(expected_cell_count)
                and text == "해당내역이 없습니다."
            ):
                empty_markers += 1
                continue
            errors.append("unexpected non-course table row")
            continue
        if len(cells) != expected_cell_count or len(links) != 1:
            errors.append("course row shape changed")
            continue
        values = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
        link_match = _DETAIL_LINK_RE.fullmatch(_clean(links[0].get("href")))
        if not link_match:
            errors.append("course detail JavaScript identity changed")
            continue
        itecd, facility_seq, reservation_seq, detail_seq, method_code, status_code, dgr = (
            link_match.groups()
        )
        if method_code not in _METHOD_LABELS or values[3] != _METHOD_LABELS[method_code]:
            errors.append(f"{reservation_seq}: recruitment method label/code mismatch")
            continue
        if (
            status_code not in _STATUS_LABELS
            or values[status_index] != _STATUS_LABELS[status_code]
        ):
            errors.append(f"{reservation_seq}: status label/code mismatch")
            continue
        if not values[1] or not values[2] or not values[3]:
            errors.append(f"{reservation_seq}: empty facility, title, or method")
            continue
        if _normalized(values[2]) != _normalized(links[0].get_text(" ", strip=True)):
            errors.append(f"{reservation_seq}: title/link mismatch")
            continue
        try:
            row_number = _integer(values[0], f"{reservation_seq}.row number")
            raw_apply_start, raw_apply_end = _date_pair(
                values[4],
                f"{reservation_seq}.application period",
                allow_reversed=True,
            )
            raw_start, raw_end = _date_pair(
                values[5], f"{reservation_seq}.use period", allow_reversed=True
            )
            current_or_future = max(raw_start, raw_end) >= cutoff
            if raw_end < raw_start and current_or_future:
                raise ValueError(
                    f"{reservation_seq}.use period: current/future range is reversed"
                )
            if raw_apply_end < raw_apply_start and current_or_future:
                raise ValueError(
                    f"{reservation_seq}.application period: current/future range is reversed"
                )
            apply_start, apply_end = sorted((raw_apply_start, raw_apply_end))
            start, end = sorted((raw_start, raw_end))
            fee, fee_amount = _fee(values[6])
            capacity_current = 0
            capacity_total = 0
            if capacity_present:
                capacity_match = _CAPACITY_RE.fullmatch(values[7])
                if not capacity_match:
                    raise ValueError(f"{reservation_seq}.capacity: invalid fraction")
                capacity_current = int(capacity_match.group(1).replace(",", ""))
                capacity_total = int(capacity_match.group(2).replace(",", ""))
                if capacity_current < 0 or capacity_total < 0:
                    raise ValueError(f"{reservation_seq}.capacity: negative value")
        except ValueError as exc:
            errors.append(_clean(exc))
            continue
        time_match = _TIME_RANGE_RE.search(values[5])
        schedule = (
            f"{time_match.group(1)} ~ {time_match.group(2)}" if time_match else ""
        )
        raw_url = _detail_url(
            source,
            category.menu_seq,
            itecd,
            facility_seq,
            reservation_seq,
            detail_seq,
            status_code,
            dgr,
        )
        if not raw_url:
            errors.append(f"{reservation_seq}: invalid generated detail URL")
            continue
        parsed.append(
            {
                "provider": DAEJEON_OK_PROVIDER,
                "provider_course_id": (
                    f"{DAEJEON_OK_PROVIDER}:{source.scope}:{itecd}:{facility_seq}:"
                    f"{reservation_seq}:{detail_seq}:{dgr or '0'}"
                ),
                "prefer_incoming_provider_course_id": True,
                "title": values[2],
                "branch": values[1],
                "branch_code": f"{itecd}:{facility_seq}",
                "preserve_branch": True,
                "provider_organizer": values[1],
                "category": category.label,
                "program_type": source.program_type,
                "raw_url": raw_url,
                "application_url": "",
                "application_type": "INFO_ONLY",
                "reservation_available": False,
                "application_method_raw": values[3],
                "status": _NORMALIZED_STATUS[status_code],
                "fee": fee,
                "fee_amount": fee_amount,
                "period": f"{start.isoformat()} ~ {end.isoformat()}",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "apply_period": f"{apply_start.isoformat()} ~ {apply_end.isoformat()}",
                "apply_start": apply_start.isoformat(),
                "apply_end": apply_end.isoformat(),
                "schedule_raw": schedule,
                "capacity": f"{capacity_current}/{capacity_total}",
                "capacity_current": capacity_current,
                "capacity_total": capacity_total,
                "collection_category": "공공예약",
                "domain_category": source.domain_category,
                "operator_type": "지자체/공공기관",
                "source_group": "municipal_reservation",
                "service_group": source.service_group,
                "service_group_policy": "locked",
                "collection_type": source.parser,
                "municipality_code": (
                    district.municipality_code if district else DAEJEON_OK_CITY_CODE
                ),
                "municipality_full_name": (
                    district.municipality_full_name if district else DAEJEON_OK_CITY_NAME
                ),
                "raw_fields": {
                    "identity": ":".join(
                        (itecd, facility_seq, reservation_seq, detail_seq, dgr or "0")
                    ),
                    "itecd": itecd,
                    "facility_seq": facility_seq,
                    "reservation_seq": reservation_seq,
                    "reservation_detail_seq": detail_seq,
                    "dgr": dgr,
                    "menu_seq": category.menu_seq,
                    "source_category": category.label,
                    "service_region_code": district.source_code if district else "000",
                    "service_region_name": district.label if district else "전체",
                    "list_page": page,
                    "source_row_number": row_number,
                    "source_method_code": method_code,
                    "source_method_label": values[3],
                    "source_fee_label": values[6],
                    "source_capacity_present": capacity_present,
                    "source_capacity_label": values[7] if capacity_present else "",
                    "source_status_code": status_code,
                    "source_status": values[status_index],
                    "source_application_period": values[4],
                    "source_use_period": values[5],
                    "source_application_period_reversed": (
                        raw_apply_end < raw_apply_start
                    ),
                    "source_use_period_reversed": raw_end < raw_start,
                },
            }
        )
    if parsed and empty_markers:
        errors.append("course rows and empty marker coexist")
    if not parsed and empty_markers != 1:
        errors.append("empty page lacks the official single empty marker")
    if parsed and page <= last:
        expected_numbers = tuple(
            range(
                total - ((page - 1) * DAEJEON_OK_PAGE_SIZE),
                total - ((page - 1) * DAEJEON_OK_PAGE_SIZE) - len(parsed),
                -1,
            )
        )
        actual_numbers = tuple(
            int(row["raw_fields"]["source_row_number"]) for row in parsed
        )
        if actual_numbers != expected_numbers:
            errors.append("source row-number sequence changed")
    if errors:
        raise ValueError(f"{label}: " + " | ".join(errors))
    return parsed, total, last


def _signature(
    rows: Iterable[Mapping[str, Any]], total: int, last: int
) -> tuple[int, int, tuple[tuple[str, str, str, str, str], ...]]:
    return total, last, tuple(_identity(row) for row in rows)


def _script_value(source: str, name: str) -> Optional[str]:
    match = re.search(_SCRIPT_VALUE_TEMPLATE.format(name=re.escape(name)), source)
    return match.group(1) if match else None


def _detail_payload(listed: Mapping[str, Any]) -> dict[str, str]:
    raw = listed["raw_fields"]
    return {
        "menuSeq": _clean(raw["menu_seq"]),
        "itecd": _clean(raw["itecd"]),
        "fcltSeq": _clean(raw["facility_seq"]),
        "rsvtSeq": _clean(raw["reservation_seq"]),
        "rsvtUseDtlSeq": _clean(raw["reservation_detail_seq"]),
        "dgr": _clean(raw["dgr"]),
    }


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}: missing object")
    return value


def _detail_row(
    listed: Mapping[str, Any], soup: BeautifulSoup, payload: Mapping[str, Any]
) -> dict[str, Any]:
    row = dict(listed)
    raw = dict(row["raw_fields"])
    identity = _clean(raw.get("identity"))
    title = _clean(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "OK예약서비스" not in title or "대전광역시" not in title:
        raise ValueError(f"{identity}: static detail title changed")
    source = str(soup)
    expected_script = {
        "publicMenuSeq": _clean(raw["menu_seq"]),
        "publicItecd": _clean(raw["itecd"]),
        "publicFcltSeq": _clean(raw["facility_seq"]),
        "publicRsvtSeq": _clean(raw["reservation_seq"]),
        "publicRsvtDtlSeq": _clean(raw["reservation_detail_seq"]),
        "publicRsvtDgr": _clean(raw["dgr"]),
    }
    for name, expected in expected_script.items():
        if _script_value(source, name) != expected:
            raise ValueError(f"{identity}: static detail {name} mismatch")
    tabs = soup.select('#tab2 a[onclick="fnSelTab(\'2\');"]')
    controls = soup.select(
        '.payment_button a.btn_pay_red[onclick="fnSetRsvtOk();"]'
    )
    if len(tabs) != 1 or _clean(tabs[0].get_text(" ", strip=True)) != "예약하기":
        raise ValueError(f"{identity}: reservation tab contract changed")
    if len(controls) != 1 or _clean(controls[0].get_text(" ", strip=True)) != "예약하기":
        raise ValueError(f"{identity}: reservation control contract changed")
    if "$('.btn_pay_red').hide()" not in source or "$('.btn_pay_red').show()" not in source:
        raise ValueError(f"{identity}: runtime reservation visibility contract changed")

    pblc = _object(payload.get("pblcVO"), f"{identity}.pblcVO")
    reservation = _object(payload.get("rsvtVO"), f"{identity}.rsvtVO")
    facility_reservation = _object(
        payload.get("fcltAllVO"), f"{identity}.fcltAllVO"
    )
    detail = _object(payload.get("dtlVO"), f"{identity}.dtlVO")
    counts = _object(payload.get("cntMap"), f"{identity}.cntMap")
    expected_identity = {
        "itecd": raw["itecd"],
        "fcltSeq": raw["facility_seq"],
        "rsvtSeq": raw["reservation_seq"],
    }
    for key, expected in expected_identity.items():
        for owner, value in (("pblcVO", pblc), ("rsvtVO", reservation)):
            if key not in value:
                if owner == "pblcVO" and key == "rsvtSeq":
                    continue
                raise ValueError(f"{identity}: {owner}.{key} missing")
            if _clean(value.get(key)) != _clean(expected):
                raise ValueError(f"{identity}: {owner}.{key} mismatch")
    for key, expected in (
        ("itecd", raw["itecd"]),
        ("fcltSeq", raw["facility_seq"]),
        ("rsvtSeq", raw["reservation_seq"]),
        ("rsvtUseDtlSeq", raw["reservation_detail_seq"]),
    ):
        if _clean(detail.get(key)) != _clean(expected):
            raise ValueError(f"{identity}: dtlVO.{key} mismatch")
    reservation_dgr = _clean(reservation.get("dgr"))
    if reservation_dgr != _clean(raw["dgr"]):
        raise ValueError(f"{identity}: reservation generation mismatch")
    if any(_clean(owner.get("useYn")) != "Y" for owner in (pblc, reservation, detail)):
        raise ValueError(f"{identity}: detail object is not enabled")
    if _normalized(reservation.get("rsvtNm")) != _normalized(row.get("title")):
        raise ValueError(f"{identity}: detail/list title mismatch")
    if _normalized(pblc.get("fcltNm")) != _normalized(row.get("branch")):
        raise ValueError(f"{identity}: detail/list facility mismatch")
    if _clean(reservation.get("rsvtMthdTpcd")) != _clean(raw["source_method_code"]):
        raise ValueError(f"{identity}: detail/list recruitment method mismatch")

    listed_apply = (
        date.fromisoformat(_clean(row["apply_start"])),
        date.fromisoformat(_clean(row["apply_end"])),
    )
    listed_use = (
        date.fromisoformat(_clean(row["start_date"])),
        date.fromisoformat(_clean(row["end_date"])),
    )
    ajax_use = (
        _milliseconds_date(detail.get("useTermBgdt"), f"{identity}.useTermBgdt"),
        _milliseconds_date(detail.get("useTermEddt"), f"{identity}.useTermEddt"),
    )
    if ajax_use != listed_use:
        raise ValueError(f"{identity}: detail/list use period mismatch")
    application_rule_code, application_rule_evidence = _application_rule_evidence(
        reservation, facility_reservation, listed_apply, identity
    )

    listed_fee = int(row["fee_amount"])
    detail_fee = _integer(detail.get("useAmt"), f"{identity}.useAmt")
    pay_flag = _clean(reservation.get("payYn"))
    source_fee_label = _clean(raw.get("source_fee_label"))
    if source_fee_label == "현장결제":
        if pay_flag != "H":
            raise ValueError(f"{identity}: on-site payment flag mismatch")
        row["fee_amount"] = detail_fee
    else:
        if detail_fee != listed_fee:
            raise ValueError(f"{identity}: detail/list fee mismatch")
        if source_fee_label == "무료" and pay_flag != "N":
            raise ValueError(f"{identity}: pay flag disagrees with fee")
        if listed_fee > 0 and pay_flag != "Y":
            raise ValueError(f"{identity}: pay flag disagrees with fee")
        if source_fee_label == "0" and pay_flag not in {"N", "Y"}:
            raise ValueError(f"{identity}: zero-fee payment flag changed")

    maximum = _integer(counts.get("MAX_LIMIT_CNT"), f"{identity}.MAX_LIMIT_CNT")
    accepted = _integer(counts.get("RCPT_CNT"), f"{identity}.RCPT_CNT")
    waiting = _integer(counts.get("WAIT_CNT"), f"{identity}.WAIT_CNT")
    _integer(counts.get("RCPT_Y_CNT"), f"{identity}.RCPT_Y_CNT")
    source_capacity_present = raw.get("source_capacity_present") is True
    if source_capacity_present and maximum != int(row["capacity_total"]):
        raise ValueError(f"{identity}: detail/list capacity mismatch")
    # The list numerator is the site's total reception count: confirmed/main
    # applications plus waiting applications, even while the rendered status
    # still says 접수중 for a few facility-specific programmes.
    displayed_application_count = accepted + waiting
    count_matches_list = (
        displayed_application_count == int(row["capacity_current"])
        if source_capacity_present
        else None
    )
    if (
        source_capacity_present
        and _clean(raw["source_status_code"]) in _ACTIVE_STATUS_CODES
        and not count_matches_list
    ):
        raise ValueError(f"{identity}: active detail/list application count mismatch")
    if not source_capacity_present:
        row["capacity_total"] = maximum
        row["capacity_current"] = displayed_application_count
        row["capacity"] = f"{displayed_application_count}/{maximum}"
    if _clean(reservation.get("rsvtUseStatTpcd")) not in {"002", "999"}:
        raise ValueError(f"{identity}: unexpected AJAX lifecycle status")

    district = next(
        (
            item
            for item in DAEJEON_OK_DISTRICTS
            if item.source_code == _clean(raw["service_region_code"])
        ),
        None,
    )
    if district is None:
        raise ValueError(f"{identity}: unknown service-region partition")
    region_depth = _clean(pblc.get("regionDepth2"))
    address = _clean(" ".join((_clean(pblc.get("addr")), _clean(pblc.get("addrDtl")))))
    if region_depth and region_depth != district.label:
        raise ValueError(f"{identity}: detail/filter district mismatch")
    if not address or "대전광역시" not in address or district.label not in address:
        raise ValueError(f"{identity}: official address lacks district evidence")

    active = _clean(raw["source_status_code"]) in _ACTIVE_STATUS_CODES
    row["application_url"] = _clean(row["raw_url"]) if active else ""
    row["reservation_available"] = active
    row["application_type"] = (
        "WAITLIST_APPLY"
        if _clean(raw["source_status_code"]) == "005"
        else ("ONLINE_RESERVATION" if active else "INFO_ONLY")
    )
    row["venue_name"] = _clean(detail.get("placeNm")) or _clean(row["branch"])
    row["venue_address"] = address
    row["address"] = address
    row["description"] = _clean(row["title"])
    row["raw_fields"] = {
        **raw,
        "detail_lifecycle_status": _clean(reservation.get("rsvtUseStatTpcd")),
        "application_rule_code": application_rule_code,
        "application_rule_evidence": application_rule_evidence,
        "detail_application_count": accepted,
        "detail_waiting_count": waiting,
        "detail_displayed_application_count": displayed_application_count,
        "detail_application_count_matches_list": count_matches_list,
        "wait_enabled": _clean(detail.get("waitUseYn")) == "Y",
        "application_control_present": active,
        "application_control_contract": "fnSetRsvtOk",
        "municipality_evidence": {
            "service_region_code": district.source_code,
            "region_depth_2": region_depth,
            "official_address": address,
            "code": district.municipality_code,
            "full_name": district.municipality_full_name,
        },
    }
    return row


def _semantic_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        _normalized(row.get("title")),
        _normalized(row.get("branch")),
        _clean(row.get("period")),
        _clean(row.get("schedule_raw")),
        _clean(row.get("capacity_total")),
    )


def _base_meta(
    error: str = "",
    source: DaejeonOkSource = DAEJEON_OK_EDUCATION_SOURCE,
) -> dict[str, Any]:
    return {
        "source_total": 0,
        "source_rows": 0,
        "global_totals": {},
        "partition_totals": {},
        "partition_pages": {},
        "page_counts": {},
        "pages": 0,
        "required_list_requests": 0,
        "list_requests": 0,
        "global_declaration_requests": 0,
        "sentinel_requests": 0,
        "stability_rechecks": 0,
        "detail_attempts": 0,
        "detail_pages": 0,
        "current_candidate_count": 0,
        "expired_count": 0,
        "returned_count": 0,
        "identity_duplicate_count": 0,
        "semantic_duplicate_group_count": 0,
        "semantic_duplicate_excess_rows": 0,
        "semantic_duplicate_policy": (
            "preserve_distinct_official_reservation_identities"
        ),
        "pagination_detected": False,
        "pagination_complete": False,
        "global_reconciliation_complete": False,
        "stable_recheck_complete": False,
        "details_complete": False,
        "snapshot_complete": False,
        "full_snapshot_validated": False,
        "source_cap_reached": False,
        "configured_collection_error": error,
        "errors": [error] if error else [],
        "no_current_data": False,
        "no_current_reason": "",
        "parser": source.parser,
        "ownership_scope": source.ownership_scope,
        "canonical_provider": DAEJEON_OK_PROVIDER,
        "canonical_url": source.canonical_url,
        "promotion_municipality_codes": [DAEJEON_OK_CITY_CODE],
        "promotion_municipality_full_names": [DAEJEON_OK_CITY_NAME],
        "district_coverage_claimed": False,
        "district_candidate_aliases": [],
        "independent_district_catalogues_included": False,
        "independent_catalogue_noncoverage_evidence": [
            dict(item) for item in DAEJEON_OK_INDEPENDENT_CATALOGUE_NONCOVERAGE
        ] if source.include_independent_noncoverage else [],
        "excluded_service_menu_seqs": {
            key: list(value) for key, value in source.excluded_menus.items()
        },
        "pii_payload_persisted": False,
    }


def _collect_daejeon_ok_scope(
    target: Any,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    *,
    source: DaejeonOkSource,
    today: Optional[date | datetime | str] = None,
    max_workers: int = DAEJEON_OK_MAX_WORKERS,
    fetch_attempts: int = DAEJEON_OK_FETCH_ATTEMPTS,
    session_factory: Optional[SessionFactory] = None,
    html_fetcher: Optional[HtmlFetcher] = None,
    json_post_fetcher: Optional[JsonPostFetcher] = None,
    dedupe_rows: Optional[DedupeRows] = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Collect one complete current/future OK reservation snapshot.

    ``max_pages`` is a per-partition safety cap and must also accommodate the
    immediate ``last + 1`` sentinel.  ``detail_limit`` is a fail-closed safety
    cap, never a partial-results limit.
    """

    if not _matches_source_target(target, source):
        meta = _base_meta(
            "target does not match the canonical Daejeon OK owner", source
        )
        return [], source.parser, meta
    if timeout < 1 or max_pages < 1 or detail_limit < 0:
        meta = _base_meta("invalid timeout, max_pages, or detail_limit", source)
        return [], source.parser, meta
    if max_workers < 1 or fetch_attempts < 1:
        meta = _base_meta("invalid max_workers or fetch_attempts", source)
        return [], source.parser, meta

    session_pool = (
        _ThreadSessionPool(_default_session_factory)
        if session_factory is None
        else None
    )
    factory = session_pool or session_factory
    worker_pool = ThreadPoolExecutor(max_workers=max_workers)

    def finish(
        rows: list[dict[str, Any]],
        meta: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        worker_pool.shutdown(wait=True)
        if session_pool is not None:
            session_pool.close()
        return rows, source.parser, meta

    get_html = html_fetcher or _default_html_fetcher
    post_json = json_post_fetcher or _default_json_post_fetcher
    cutoff = _today(today)
    global_totals: dict[str, int] = {}
    partition_totals: dict[str, int] = {}
    partition_pages: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    first_signatures: dict[
        str, tuple[int, int, tuple[tuple[str, str, str, str, str], ...]]
    ] = {}
    source_rows: list[dict[str, Any]] = []
    list_requests = 0
    global_declaration_requests = 0
    sentinel_requests = 0
    stability_rechecks = 0
    required_list_requests = 0
    current_count = 0
    detail_attempts = 0
    pagination_complete = False
    global_reconciliation_complete = False
    stable_recheck_complete = False

    def fetch_list(
        category: DaejeonOkCategory,
        district: Optional[DaejeonOkDistrict],
        page: int,
    ) -> tuple[list[dict[str, Any]], int, int]:
        district_code = district.source_code if district else "000"
        url = _list_url(source, category.menu_seq, district_code, page)
        soup = _fetch_html(
            url,
            session_factory=factory,
            fetcher=get_html,
            timeout=timeout,
            attempts=fetch_attempts,
            label=f"list {category.menu_seq}/{district_code} page {page}",
        )
        return _parse_list_page(soup, category, district, page, cutoff, source)

    try:
        initial_tasks: list[
            tuple[DaejeonOkCategory, Optional[DaejeonOkDistrict], int]
        ] = [
            (category, None, 1) for category in source.categories
        ] + [
            (category, district, 1)
            for category in source.categories
            for district in DAEJEON_OK_DISTRICTS
        ]
        initial_results: dict[
            tuple[str, str], tuple[list[dict[str, Any]], int, int]
        ] = {}
        futures = {
            worker_pool.submit(fetch_list, category, district, page): (
                category,
                district,
            )
            for category, district, page in initial_tasks
        }
        for future in as_completed(futures):
            category, district = futures[future]
            code = district.source_code if district else "000"
            initial_results[(category.menu_seq, code)] = future.result()
            list_requests += 1
            if district is None:
                global_declaration_requests += 1

        for category in source.categories:
            global_rows, global_total, _global_last = initial_results[
                (category.menu_seq, "000")
            ]
            expected_global_first = min(DAEJEON_OK_PAGE_SIZE, global_total)
            if len(global_rows) != expected_global_first:
                raise ValueError(
                    f"global {category.menu_seq} page 1 returned {len(global_rows)} "
                    f"of expected {expected_global_first}"
                )
            global_totals[category.menu_seq] = global_total
            for district in DAEJEON_OK_DISTRICTS:
                key = f"{category.menu_seq}:{district.source_code}"
                rows, total, last = initial_results[
                    (category.menu_seq, district.source_code)
                ]
                if last + 1 > max_pages:
                    raise ValueError(
                        f"partition {key} requires page {last + 1} sentinel "
                        f"beyond max_pages {max_pages}"
                    )
                expected = min(DAEJEON_OK_PAGE_SIZE, total)
                if len(rows) != expected:
                    raise ValueError(
                        f"partition {key} page 1 returned {len(rows)} "
                        f"of expected {expected}"
                    )
                partition_totals[key] = total
                partition_pages[key] = last
                page_counts[f"{key}:1"] = len(rows)
                first_signatures[key] = _signature(rows, total, last)
                source_rows.extend(rows)

        for category in source.categories:
            partition_sum = sum(
                partition_totals[f"{category.menu_seq}:{district.source_code}"]
                for district in DAEJEON_OK_DISTRICTS
            )
            if partition_sum != global_totals[category.menu_seq]:
                raise ValueError(
                    f"category {category.menu_seq} district total {partition_sum} "
                    f"does not match global total {global_totals[category.menu_seq]}"
                )
        global_reconciliation_complete = True

        remaining_tasks: list[
            tuple[DaejeonOkCategory, DaejeonOkDistrict, int, bool]
        ] = []
        for category in source.categories:
            for district in DAEJEON_OK_DISTRICTS:
                key = f"{category.menu_seq}:{district.source_code}"
                last = partition_pages[key]
                remaining_tasks.extend(
                    (category, district, page, False)
                    for page in range(2, last + 1)
                )
                remaining_tasks.append((category, district, last + 1, True))
        required_list_requests = (
            len(initial_tasks) + len(remaining_tasks) + len(partition_totals)
        )

        futures = {
            worker_pool.submit(fetch_list, category, district, page): (
                category,
                district,
                page,
                sentinel,
            )
            for category, district, page, sentinel in remaining_tasks
        }
        for future in as_completed(futures):
            category, district, page, sentinel = futures[future]
            key = f"{category.menu_seq}:{district.source_code}"
            rows, total, last = future.result()
            list_requests += 1
            if total != partition_totals[key] or last != partition_pages[key]:
                raise ValueError(f"partition {key} pagination declaration drifted")
            if sentinel:
                sentinel_requests += 1
                page_counts[f"{key}:{page}:sentinel"] = len(rows)
                if rows:
                    raise ValueError(f"partition {key} sentinel page is not empty")
                continue
            expected = min(
                DAEJEON_OK_PAGE_SIZE,
                total - ((page - 1) * DAEJEON_OK_PAGE_SIZE),
            )
            if len(rows) != expected:
                raise ValueError(
                    f"partition {key} page {page} returned {len(rows)} "
                    f"of expected {expected}"
                )
            page_counts[f"{key}:{page}"] = len(rows)
            source_rows.extend(rows)

        recheck_tasks = [
            (category, district, 1)
            for category in source.categories
            for district in DAEJEON_OK_DISTRICTS
        ]
        futures = {
            worker_pool.submit(fetch_list, category, district, page): (
                category,
                district,
            )
            for category, district, page in recheck_tasks
        }
        for future in as_completed(futures):
            category, district = futures[future]
            key = f"{category.menu_seq}:{district.source_code}"
            rows, total, last = future.result()
            list_requests += 1
            stability_rechecks += 1
            if _signature(rows, total, last) != first_signatures[key]:
                raise ValueError(f"partition {key} changed during stable recheck")

        if list_requests != required_list_requests:
            raise ValueError("not every required list request completed")
        pagination_complete = True
        stable_recheck_complete = True
        identities = [_identity(row) for row in source_rows]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate official identity across categories/regions/pages")
        declared_source_total = sum(global_totals.values())
        if len(source_rows) != declared_source_total:
            raise ValueError("combined source rows do not match global declarations")

        current = [
            row
            for row in source_rows
            if date.fromisoformat(_clean(row["end_date"])) >= cutoff
        ]
        current_count = len(current)
        if len(current) > detail_limit:
            meta = {
                **_base_meta(
                    f"current/future detail count {len(current)} exceeds "
                    f"detail_limit {detail_limit}",
                    source,
                ),
                "source_total": declared_source_total,
                "source_rows": len(source_rows),
                "global_totals": global_totals,
                "partition_totals": partition_totals,
                "partition_pages": partition_pages,
                "page_counts": page_counts,
                "pages": sum(partition_pages.values()),
                "required_list_requests": required_list_requests,
                "list_requests": list_requests,
                "global_declaration_requests": global_declaration_requests,
                "sentinel_requests": sentinel_requests,
                "stability_rechecks": stability_rechecks,
                "current_candidate_count": len(current),
                "expired_count": len(source_rows) - len(current),
                "pagination_detected": any(v > 1 for v in partition_pages.values()),
                "pagination_complete": True,
                "global_reconciliation_complete": True,
                "stable_recheck_complete": True,
                "source_cap_reached": True,
            }
            return finish([], meta)

        detailed: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

        def fetch_detail(
            listed: Mapping[str, Any],
        ) -> tuple[tuple[str, str, str, str, str], dict[str, Any]]:
            identity = _identity(listed)
            detail_soup, ajax = _fetch_detail_pair(
                _clean(listed["raw_url"]),
                _detail_payload(listed),
                ajax_endpoint=source.detail_ajax_endpoint,
                session_factory=factory,
                html_fetcher=get_html,
                json_fetcher=post_json,
                timeout=timeout,
                attempts=fetch_attempts,
                label=f"detail {'/'.join(identity)}",
            )
            return identity, _detail_row(listed, detail_soup, ajax)

        detail_errors: list[str] = []
        detail_attempts = len(current)
        if current:
            futures = {worker_pool.submit(fetch_detail, row): row for row in current}
            for future in as_completed(futures):
                listed = futures[future]
                identity = _identity(listed)
                try:
                    returned_identity, row = future.result()
                    if returned_identity != identity:
                        raise ValueError("worker identity mismatch")
                    if identity in detailed:
                        raise ValueError("duplicate detail identity")
                    detailed[identity] = row
                except Exception as exc:
                    detail_errors.append(
                        f"detail {'/'.join(identity)}: {_clean(exc)}"
                    )
        if detail_errors:
            raise ValueError(" | ".join(sorted(detail_errors)))
        if len(detailed) != len(current):
            raise ValueError("not every current/future detail was collected")

        rows = [detailed[_identity(row)] for row in current]
        if dedupe_rows is not None:
            rows = list(dedupe_rows(rows))
            if len(rows) != len(current):
                raise ValueError("shared row dedupe changed official identity cardinality")
        course_ids = [_clean(row.get("provider_course_id")) for row in rows]
        raw_urls = [_clean(row.get("raw_url")) for row in rows]
        if len(course_ids) != len(set(course_ids)) or len(raw_urls) != len(set(raw_urls)):
            raise ValueError("generated course IDs or detail URLs are not unique")
        semantic_counts = Counter(_semantic_key(row) for row in rows)
        semantic_duplicate_group_count = sum(
            1 for count in semantic_counts.values() if count > 1
        )
        semantic_duplicate_excess_rows = sum(
            count - 1 for count in semantic_counts.values() if count > 1
        )

        meta = {
            **_base_meta(source=source),
            "source_total": declared_source_total,
            "source_rows": len(source_rows),
            "global_totals": global_totals,
            "partition_totals": partition_totals,
            "partition_pages": partition_pages,
            "page_counts": page_counts,
            "pages": sum(partition_pages.values()),
            "required_list_requests": required_list_requests,
            "list_requests": list_requests,
            "global_declaration_requests": global_declaration_requests,
            "sentinel_requests": sentinel_requests,
            "stability_rechecks": stability_rechecks,
            "detail_attempts": len(current),
            "detail_pages": len(current),
            "current_candidate_count": len(current),
            "expired_count": len(source_rows) - len(current),
            "returned_count": len(rows),
            "identity_duplicate_count": 0,
            "semantic_duplicate_group_count": semantic_duplicate_group_count,
            "semantic_duplicate_excess_rows": semantic_duplicate_excess_rows,
            "pagination_detected": any(v > 1 for v in partition_pages.values()),
            "pagination_complete": True,
            "global_reconciliation_complete": True,
            "stable_recheck_complete": True,
            "details_complete": True,
            "snapshot_complete": True,
            "full_snapshot_validated": True,
            "configured_collection_error": "",
            "errors": [],
            "no_current_data": not rows,
            "no_current_reason": (
                "all declared OK courses ended before the reference day" if not rows else ""
            ),
            "row_location_municipality_codes": sorted(
                {_clean(row.get("municipality_code")) for row in rows}
            ),
            "municipality_counts": dict(
                Counter(_clean(row.get("municipality_full_name")) for row in rows)
            ),
            "branch_counts": dict(Counter(_clean(row.get("branch")) for row in rows)),
            "status_counts": dict(Counter(_clean(row.get("status")) for row in rows)),
        }
        return finish(rows, meta)
    except Exception as exc:
        error = _clean(exc)
        meta = {
            **_base_meta(error, source),
            "source_total": sum(global_totals.values()),
            "source_rows": len(source_rows),
            "global_totals": global_totals,
            "partition_totals": partition_totals,
            "partition_pages": partition_pages,
            "page_counts": page_counts,
            "pages": sum(partition_pages.values()),
            "required_list_requests": required_list_requests,
            "list_requests": list_requests,
            "global_declaration_requests": global_declaration_requests,
            "sentinel_requests": sentinel_requests,
            "stability_rechecks": stability_rechecks,
            "pagination_detected": any(v > 1 for v in partition_pages.values()),
            "pagination_complete": pagination_complete,
            "global_reconciliation_complete": global_reconciliation_complete,
            "stable_recheck_complete": stable_recheck_complete,
            "current_candidate_count": current_count,
            "expired_count": max(0, len(source_rows) - current_count),
            "detail_attempts": detail_attempts,
            "detail_pages": 0,
        }
        return finish([], meta)


def collect_daejeon_ok_education(
    target: Any,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return _collect_daejeon_ok_scope(
        target,
        timeout,
        max_pages,
        detail_limit,
        source=DAEJEON_OK_EDUCATION_SOURCE,
        **kwargs,
    )


def collect_daejeon_ok_experience(
    target: Any,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    return _collect_daejeon_ok_scope(
        target,
        timeout,
        max_pages,
        detail_limit,
        source=DAEJEON_OK_EXPERIENCE_SOURCE,
        **kwargs,
    )


def collect_daejeon_ok_courses(
    target: Any,
    timeout: int,
    max_pages: int,
    detail_limit: int,
    **kwargs: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    source = _source_for_target(target)
    if source is None:
        meta = _base_meta("target does not match a canonical Daejeon OK owner")
        return [], DAEJEON_OK_PARSER, meta
    return _collect_daejeon_ok_scope(
        target,
        timeout,
        max_pages,
        detail_limit,
        source=source,
        **kwargs,
    )


collect = collect_daejeon_ok_courses
